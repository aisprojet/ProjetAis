from flask import Flask, request, render_template, redirect, url_for, session
from flask_login import LoginManager, UserMixin, login_user, login_required
from ldap3 import Server, Connection, SIMPLE, ALL, MODIFY_ADD
import requests
import subprocess
import os
import shutil
import uuid
import paramiko
import logging
import json
import tempfile
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Charger les variables d'environnement depuis .env
load_dotenv()

# Logging principal
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Logger dédié aux emails
mail_logger = logging.getLogger('mail')
mail_logger.setLevel(logging.DEBUG)
mail_handler = logging.FileHandler('/var/log/flask/mail.log')
mail_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
mail_logger.addHandler(mail_handler)

app = Flask(__name__)
app.secret_key = 'poseidon'

# ============== CONFIGURATION ==============

# LDAP
SERVEUR_LDAP     = "ldaps://ldap.techvault.fr"
BASE_DN_LDAP     = "dc=techvault,dc=fr"
ADMIN_DN         = "cn=admin,dc=techvault,dc=fr"
ADMIN_PASSWORD   = "poseidon"
GROUPE_ETUDIANTS = "cn=etudiants,ou=groups,dc=techvault,dc=fr"
GROUPE_GUESTS    = "cn=guests,ou=groups,dc=techvault,dc=fr"

# Kerberos
REALM            = "TECHVAULT.FR"
KADMIN_PRINCIPAL = "flask-admin@TECHVAULT.FR"
KADMIN_SERVER    = "kerberos.techvault.fr"
KADMIN_KEYTAB    = "/etc/flask-admin.keytab"

# NFS
NFS_SERVER  = "nfs.techvault.fr"
NFS_USER    = "ais"
NFS_SSH_KEY = "/var/lib/www-data/.ssh/nfs_admin_key"

#Tofu
PM_API   = "https://192.168.1.200:8006/api2/json"
PM_NODE  = "pve"
PM_TOKEN_ID   = "Administrateur@pve!NEWTOKEN"
PM_TOKEN_SECRET = "3cf465e6-3981-4909-bf60-1bbbcb402ed5"
TF_DIR = "/srv/tofu"

# Ansible
ANSIBLE_DIR       = "/srv/ansible"
ANSIBLE_PLAYBOOK  = f"{ANSIBLE_DIR}/playbook.yml"
ANSIBLE_INVENTORY = f"{ANSIBLE_DIR}/inventory.ini"

# SMTP - Gmail (configuré via .env)
SMTP_SERVER   = "smtp.gmail.com"
SMTP_PORT     = 587
SMTP_EMAIL    = os.environ.get("SMTP_EMAIL", "noreply@techvault.fr")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")

# Durée invités
DUREE_GUEST = 7


# ============== FONCTIONS UTILITAIRES ==============

def obtenir_prochain_uid():
    serveur = Server(SERVEUR_LDAP, get_info=ALL)
    try:
        connexion = Connection(serveur, user=ADMIN_DN,
                               password=ADMIN_PASSWORD, auto_bind=True)
        connexion.search(
            search_base=BASE_DN_LDAP,
            search_filter='(objectClass=posixAccount)',
            attributes=['uidNumber']
        )
        uids = [int(entry.uidNumber.value) for entry in connexion.entries]
        connexion.unbind()
        max_uid = max(uids) if uids else 10000
        return str(max_uid + 1)
    except Exception as e:
        logger.error(f"Erreur récupération UID: {e}")
        return "10001"


def creer_principal_kerberos(username, password):
    try:
        cmd = (f'/usr/bin/kadmin -s {KADMIN_SERVER} -p {KADMIN_PRINCIPAL} '
               f'-k -t {KADMIN_KEYTAB} -q "addprinc -pw {password} {username}@{REALM}"')
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, "Principal Kerberos créé"
        else:
            if "already exists" in result.stderr or "already exists" in result.stdout:
                return True, "Principal déjà existant"
            return False, f"Erreur Kerberos: {result.stderr}"
    except subprocess.TimeoutExpired:
        return False, "Timeout connexion KDC"
    except Exception as e:
        return False, f"Erreur Kerberos: {str(e)}"


def creer_dossier_nfs_distant(username, uid, gid):
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(hostname=NFS_SERVER, username=NFS_USER,
                    key_filename=NFS_SSH_KEY, timeout=10)
        commands = [
            f"sudo mkdir -p /srv/nfs/prive/{username}",
            f"sudo chown {uid}:{gid} /srv/nfs/prive/{username}",
            f"sudo chmod 700 /srv/nfs/prive/{username}"
        ]
        for cmd in commands:
            stdin, stdout, stderr = ssh.exec_command(cmd)
            stdout.channel.recv_exit_status()
        ssh.close()
        return True, "Dossier NFS créé"
    except Exception as e:
        return False, f"Erreur NFS: {str(e)}"

def vm_existe_deja(vmid: int) -> bool:
    try:
        r = requests.get(
            f"{PM_API}/nodes/{PM_NODE}/qemu/{vmid}/status/current",
            headers={"Authorization": f"PVEAPIToken={PM_TOKEN_ID}={PM_TOKEN_SECRET}"},
            verify=False, timeout=5
        )
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"Erreur existence VM {vmid} : {e}")
        return False

def get_vm_ip(vmid: int) -> str:
    """Récupère l’IP depuis l’agent QEMU-guest de la VM."""
    try:
        r = requests.get(
            f"{PM_API}/nodes/{PM_NODE}/qemu/{vmid}/agent/network-get-interfaces",
            headers={"Authorization": f"PVEAPIToken={PM_TOKEN_ID}={PM_TOKEN_SECRET}"},
            verify=False, timeout=5
        )
        if r.status_code != 200:
            return ""
        data = r.json()
        for iface in data["result"]:
            for addr in iface.get("ip-addresses", []):
                if addr["ip-address-type"] == "ipv4" and not addr["ip-address"].startswith("127"):
                    return addr["ip-address"]
        return ""
    except Exception as e:
        logger.warning(f"Impossible de récupérer IP VM {vmid} : {e}")
        return ""


def get_vnc_ticket(vmid: int) -> dict:
    """Génère un ticket VNC via l’API Proxmox."""
    try:
        r = requests.post(
            f"{PM_API}/nodes/{PM_NODE}/qemu/{vmid}/vncproxy",
            headers={"Authorization": f"PVEAPIToken={PM_TOKEN_ID}={PM_TOKEN_SECRET}"},
            verify=False, timeout=10
        )
        if r.status_code == 200:
            return r.json()["data"]
        logger.error(f"Erreur VNC ticket: {r.status_code} - {r.text}")
        return None
    except Exception as e:
        logger.error(f"Exception VNC ticket: {e}")
        return None


def get_vmid_from_uid(uid: str) -> int:
    """Calcule le VM ID à partir de l’UID (2000 + uid)."""
    return 2000 + int(uid)


def deployer_vm_tofu(nom: str, email: str, uid: str, gid: str):
    try:
        tf_vars = {
            "api": PM_API, "token_id": PM_TOKEN_ID, "token_secret": PM_TOKEN_SECRET,
            "node": PM_NODE, "storage": "local-lvm", "modele": "Modele-Debian-cloudinit",
            "vm_name": f"vm-{nom}", "vm_user": nom, "vm_uid": uid, "vm_gid": gid,
            "nombre": 1
        }
        vars_file = f"/srv/tofu/vars/{nom}.tfvars.json"
        os.makedirs("/srv/tofu/vars", exist_ok=True)
        with open(vars_file, "w") as f:
            json.dump(tf_vars, f)

        result = subprocess.run(
            ["/srv/tofu/scripts/deploy-vm.sh", nom],
            capture_output=True, text=True, timeout=600
        )
        if result.returncode != 0:
            logger.error(f"Tofu stderr: {result.stderr}")
            return False, "Échec du déploiement Tofu"

        ip_vm = result.stdout.strip().splitlines()[-1]
        if not ip_vm:
            logger.error(f"IP non récupérée pour {nom}")
            return False, "VM créée mais IP non disponible"

        logger.info(f"IP récupérée pour {nom} : {ip_vm}")
        return True, ip_vm

    except subprocess.TimeoutExpired:
        logger.error("Tofu a dépassé le temps autorisé (10 min)")
        return False, "Timeout Tofu (>10 min)"
    except Exception as e:
        logger.exception("Erreur déploiement Tofu")
        return False, str(e)


def obtenir_uid_par_email(email: str) -> str:
    """Récupère l’uidNumber depuis LDAP."""
    serveur = Server(SERVEUR_LDAP, get_info=ALL)
    with Connection(serveur, user=ADMIN_DN, password=ADMIN_PASSWORD, auto_bind=True) as conn:
        conn.search(search_base=BASE_DN_LDAP,
                    search_filter=f'(mail={email})',
                    attributes=['uidNumber'])
        if conn.entries:
            return str(conn.entries[0].uidNumber.value)
    return "10001"


def rollback_utilisateur(nom):
    """Nettoie LDAP et Kerberos si la VM échoue."""
    try:
        subprocess.run([
            "ldapdelete", "-x", "-D", ADMIN_DN, "-w", ADMIN_PASSWORD,
            f"uid={nom},ou=users,{BASE_DN_LDAP}"
        ], capture_output=True)
        subprocess.run([
            "kadmin", "-s", KADMIN_SERVER, "-p", KADMIN_PRINCIPAL,
            "-k", "-t", KADMIN_KEYTAB, "-q", f"delprinc -force {nom}@{REALM}"
        ], capture_output=True)
        logger.info(f"[ROLLBACK] Utilisateur {nom} supprimé.")
    except Exception as e:
        logger.error(f"[ROLLBACK] Erreur : {e}")

def lancer_ansible(nom, email, uid, gid, inventory_path):
    """
    Déploie / configure la VM via ansible-playbook.
    Gestion complète : HOME temporaire, vars file, logs.
    """
    ansible_home = "/var/lib/www-data"
    os.makedirs(ansible_home, exist_ok=True)
    os.chmod(ansible_home, 0o700)

    ansible_bin = shutil.which("ansible-playbook")
    if not ansible_bin:
        logger.error("ansible-playbook introuvable dans PATH")
        return False, "ansible-playbook non installé ou non visible par www-data"

    vars_data = {"vm_user": nom, "vm_email": email, "vm_uid": uid, "vm_gid": gid}
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(vars_data, f)
            vars_file = f.name

        env = os.environ.copy()
        env["HOME"] = ansible_home
        env["ANSIBLE_LOCAL_TEMP"] = os.path.join(ansible_home, "tmp")
        os.makedirs(env["ANSIBLE_LOCAL_TEMP"], exist_ok=True)


        cmd = [
            ansible_bin,
            "-i", inventory_path,
            ANSIBLE_PLAYBOOK,
            "-e", f"@{vars_file}",
            "-v"
        ]

        logger.info(f"Lancement Ansible : {' '.join(cmd)}")
        completed = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=600
        )

        os.unlink(vars_file)

        if completed.returncode == 0:
            logger.info(f"[OK] Ansible terminé pour {nom}")
            return True, "VM configurée avec succès"
        else:
            logger.error(f"[ERREUR] Ansible stdout : {completed.stdout}")
            logger.error(f"[ERREUR] Ansible stderr : {completed.stderr}")
            return False, f"Erreur configuration VM : {completed.stderr[:200]}"

    except subprocess.TimeoutExpired:
        logger.error("Ansible a dépassé le temps autorisé (10 min)")
        return False, "Timeout Ansible (>10 min)"
    except Exception as e:
        logger.exception("Exception non anticipée lors d'Ansible")
        return False, f"Exception Ansible : {str(e)}"

def envoyer_mail_etudiant(email, nom, vmid=None, ip_vm=None, retries=3):
    """
    Envoie un email à l'étudiant avec retries et logs détaillés.
    Retourne True si succès, False si échec après toutes les tentatives.
    """
    mail_logger.info(f"=== Début envoi email étudiant à {email} (tentatives: {retries}) ===")
    mail_logger.info(f"Destinataire: {email}, Nom: {nom}, VMID: {vmid}, IP: {ip_vm}")

    # Calculer le lien noVNC direct pour la console
    if vmid:
        # Lien direct vers la console noVNC de la VM
        # Le vmname est construit comme vm-{nom}
        vmname = f"vm-{nom}"
        console_url = f"https://proxmox.techvault.fr/?console=kvm&novnc=1&vmid={vmid}&vmname={vmname}&node={PM_NODE}&resize=off&cmd="
    else:
        console_url = "https://proxmox.techvault.fr"

    msg = MIMEMultipart("alternative")
    msg['Subject'] = "✅ Votre VM TechVault est prête !"
    msg['From']    = SMTP_EMAIL
    msg['To']      = email

    vmid_display = vmid if vmid else "Non attribué"
    ip_display = ip_vm if ip_vm else "Non disponible"

    contenu_html = f"""
    <html><body style="font-family: Arial, sans-serif;">
    <h2>Bonjour {nom},</h2>
    <p>Votre compte et votre VM ont été créés avec succès sur <strong>TechVault</strong>.</p>

    <h3>🔑 Vos identifiants</h3>
    <ul>
        <li><strong>Login :</strong> {nom}</li>
        <li><strong>Mot de passe :</strong> celui choisi lors de l'inscription</li>
    </ul>

    <h3>🖥️ Accès à votre VM</h3>
    <ul>
        <li><strong>VM ID :</strong> {vmid_display}</li>
        <li><strong>IP :</strong> {ip_display}</li>
        <li><strong>Console noVNC (accès direct) :</strong> <a href="{console_url}">{console_url}</a></li>
        <li><strong>Dossier personnel (NFS) :</strong> /mnt/prive/{nom}</li>
        <li><strong>Dossier commun (NFS) :</strong> /mnt/partage</li>
    </ul>

    <p>⚠️ Ne partagez pas vos identifiants.</p>
    <p>— L'équipe TechVault</p>
    </body></html>
    """
    msg.attach(MIMEText(contenu_html, "html"))

    for attempt in range(1, retries + 1):
        mail_logger.info(f"Tentative {attempt}/{retries} pour {email}")
        try:
            mail_logger.debug(f"Connexion SMTP: {SMTP_SERVER}:{SMTP_PORT}")
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as serveur:
                mail_logger.debug("STARTTLS...")
                serveur.starttls()
                mail_logger.debug(f"Login SMTP avec {SMTP_EMAIL}...")
                serveur.login(SMTP_EMAIL, SMTP_PASSWORD)
                mail_logger.debug(f"Envoi du mail à {email}...")
                serveur.sendmail(SMTP_EMAIL, email, msg.as_string())

            mail_logger.info(f"[SUCCÈS] Mail envoyé à {email} après {attempt} tentative(s)")
            logger.info(f"[OK] Mail envoyé à {email}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            mail_logger.error(f"[ERREUR AUTH] Échec authentification SMTP: {e}")
            logger.error(f"[ERREUR] Mail auth: {e}")
            # Pas de retry pour auth, ça ne changera pas
            return False

        except smtplib.SMTPConnectError as e:
            mail_logger.error(f"[ERREUR CONNECT] Impossible de se connecter à {SMTP_SERVER}:{SMTP_PORT}: {e}")
            if attempt < retries:
                wait_time = attempt * 2
                mail_logger.warning(f"Attente {wait_time}s avant retry...")
                time.sleep(wait_time)
            continue

        except smtplib.SMTPException as e:
            mail_logger.error(f"[ERREUR SMTP] {type(e).__name__}: {e}")
            if attempt < retries:
                wait_time = attempt * 2
                mail_logger.warning(f"Attente {wait_time}s avant retry...")
                time.sleep(wait_time)
            continue

        except Exception as e:
            mail_logger.error(f"[ERREUR] {type(e).__name__}: {e}")
            if attempt < retries:
                wait_time = attempt * 2
                mail_logger.warning(f"Attente {wait_time}s avant retry...")
                time.sleep(wait_time)
            continue

    mail_logger.error(f"[ÉCHEC] Mail non envoyé à {email} après {retries} tentatives")
    logger.error(f"[ERREUR] Mail: échec après {retries} tentatives")
    return False


def envoyer_mail_guest(email, nom, mot_de_passe_temp, retries=3):
    """
    Envoie un email à l'invité avec retries et logs détaillés.
    Retourne True si succès, False si échec après toutes les tentatives.
    """
    mail_logger.info(f"=== Début envoi email guest à {email} (tentatives: {retries}) ===")

    msg = MIMEMultipart("alternative")
    msg['Subject'] = "🔑 Votre accès invité TechVault"
    msg['From']    = SMTP_EMAIL
    msg['To']      = email

    contenu_html = f"""
    <html><body style="font-family: Arial, sans-serif;">
    <h2>Bonjour,</h2>
    <p>Votre accès invité <strong>TechVault</strong> a été créé. Il est valable <strong>7 jours</strong>.</p>

    <h3>🔑 Vos identifiants temporaires</h3>
    <ul>
        <li><strong>Login :</strong> {nom}</li>
        <li><strong>Mot de passe :</strong> {mot_de_passe_temp}</li>
    </ul>

    <h3>🖥️ Accès</h3>
    <ul>
        <li><a href="http://proxmox.techvault.fr:8006">http://proxmox.techvault.fr:8006</a></li>
        <li><strong>Dossier commun :</strong> /mnt/partage (lecture seule)</li>
    </ul>

    <p>⚠️ Ce compte expirera automatiquement dans 7 jours.</p>
    <p>— L'équipe TechVault</p>
    </body></html>
    """
    msg.attach(MIMEText(contenu_html, "html"))

    for attempt in range(1, retries + 1):
        mail_logger.info(f"Tentative {attempt}/{retries} pour {email}")
        try:
            mail_logger.debug(f"Connexion SMTP: {SMTP_SERVER}:{SMTP_PORT}")
            with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=10) as serveur:
                mail_logger.debug("STARTTLS...")
                serveur.starttls()
                mail_logger.debug(f"Login SMTP avec {SMTP_EMAIL}...")
                serveur.login(SMTP_EMAIL, SMTP_PASSWORD)
                mail_logger.debug(f"Envoi du mail à {email}...")
                serveur.sendmail(SMTP_EMAIL, email, msg.as_string())

            mail_logger.info(f"[SUCCÈS] Mail guest envoyé à {email} après {attempt} tentative(s)")
            logger.info(f"[OK] Mail guest envoyé à {email}")
            return True

        except smtplib.SMTPAuthenticationError as e:
            mail_logger.error(f"[ERREUR AUTH] Échec authentification SMTP: {e}")
            return False

        except smtplib.SMTPConnectError as e:
            mail_logger.error(f"[ERREUR CONNECT] Impossible de se connecter à {SMTP_SERVER}:{SMTP_PORT}: {e}")
            if attempt < retries:
                time.sleep(attempt * 2)
            continue

        except smtplib.SMTPException as e:
            mail_logger.error(f"[ERREUR SMTP] {type(e).__name__}: {e}")
            if attempt < retries:
                time.sleep(attempt * 2)
            continue

        except Exception as e:
            mail_logger.error(f"[ERREUR] {type(e).__name__}: {e}")
            if attempt < retries:
                time.sleep(attempt * 2)
            continue

    mail_logger.error(f"[ÉCHEC] Mail guest non envoyé à {email} après {retries} tentatives")
    logger.error(f"[ERREUR] Mail guest: échec après {retries} tentatives")
    return False


def authentifier_ldap(nom_utilisateur, mot_de_passe):
    serveur = Server(SERVEUR_LDAP, get_info=ALL)
    try:
        user_dn = f'uid={nom_utilisateur},ou=users,{BASE_DN_LDAP}'
        connexion = Connection(serveur, user=user_dn,
                               password=mot_de_passe, authentication=SIMPLE)
        if connexion.bind():
            connexion.unbind()
            return True
    except Exception as e:
        logger.error(f"Erreur auth LDAP: {e}")
    return False


def creer_utilisateur_ldap(email, mot_de_passe, groupe="etudiants", duree_jours=0):
    serveur = Server(SERVEUR_LDAP, get_info=ALL)

    if not email or '@' not in email:
        return False, "Email invalide", None, None

    try:
        connexion_admin = Connection(serveur, user=ADMIN_DN,
                                     password=ADMIN_PASSWORD, auto_bind=True)
        nom = email.split('@')[0]
        user_dn = f'uid={nom},ou=users,{BASE_DN_LDAP}'

        if connexion_admin.search(user_dn, '(objectClass=*)'):
            connexion_admin.unbind()
            return False, "Cet utilisateur existe déjà", None, None

        if groupe == "guests":
            gid_number     = '5002'
            groupe_dn      = GROUPE_GUESTS
            message_groupe = "invités"
        else:
            gid_number     = '5001'
            groupe_dn      = GROUPE_ETUDIANTS
            message_groupe = "étudiants"

        uid_number = obtenir_prochain_uid()

        attributs = {
            'objectClass':   ['inetOrgPerson', 'posixAccount', 'top', 'shadowAccount'],
            'uid':           nom,
            'cn':            nom,
            'sn':            nom,
            'mail':          email,
            'userPassword':  mot_de_passe,
            'uidNumber':     uid_number,
            'gidNumber':     gid_number,
            'homeDirectory': f"/mnt/prive/{nom}",
            'loginShell':    '/bin/bash'
        }

        if groupe == "guests" and duree_jours > 0:
            date_expiration = datetime.now() + timedelta(days=duree_jours)
            jours_unix = (date_expiration - datetime(1970, 1, 1)).days
            attributs['shadowExpire'] = str(jours_unix)

        resultat_add = connexion_admin.add(user_dn, attributes=attributs)
        if not resultat_add:
            connexion_admin.unbind()
            return False, f"Erreur création LDAP: {connexion_admin.result}", None, None

        connexion_admin.modify(groupe_dn, {'memberUid': [(MODIFY_ADD, [nom])]})
        connexion_admin.unbind()

        # Kerberos
        success_krb, msg_krb = creer_principal_kerberos(nom, mot_de_passe)
        if not success_krb:
            return False, f"Utilisateur LDAP créé mais erreur Kerberos: {msg_krb}", None, None

        # NFS (pas pour les guests)
        if groupe != "guests":
            success_nfs, msg_nfs = creer_dossier_nfs_distant(nom, uid_number, gid_number)
            if not success_nfs:
                return False, f"Utilisateur créé mais erreur NFS: {msg_nfs}", None, None

        return True, f"Compte créé avec succès! (UID: {uid_number}, Groupe: {message_groupe})", uid_number, gid_number

    except Exception as e:
        return False, f"Erreur création compte: {str(e)}", None, None


# ============== ROUTES ==============

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email        = request.form.get('email')
        mot_de_passe = request.form.get('mot_de_passe')
        nom = email.split('@')[0]  # Extraire le nom avant l'authentification
        if authentifier_ldap(nom, mot_de_passe):
            uid = obtenir_uid_par_email(email)
            vmid = get_vmid_from_uid(uid)

            # Vérifier si VM existe déjà
            vm_existante = vm_existe_deja(vmid)

            if not vm_existante:
                # Nouvelle VM à créer
                tofu_ok, ip_vm = deployer_vm_tofu(nom, email, uid, "5001")
                if not tofu_ok:
                    return render_template('login.html', error="Impossible de démarrer votre VM")
                # Envoyer l'email avec les infos de la VM
                envoyer_mail_etudiant(email, nom, vmid=vmid, ip_vm=ip_vm)
            else:
                # VM existe déjà, récupérer l'IP
                ip_vm = get_vm_ip(vmid)

            session['user']   = email
            session['vm_ip']  = ip_vm
            return redirect(url_for('dashboard'))
        return render_template('login.html', error="Identifiants incorrects")
    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('dashboard.html', user=session['user'])


@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email        = request.form.get('email')
        mot_de_passe = request.form.get('mot_de_passe')

        # 1. Création compte LDAP
        succes, message, uid, gid = creer_utilisateur_ldap(
            email, mot_de_passe, groupe="etudiants"
        )

        # 2. Initialisation des variables Tofu
        tofu_ok, ip_vm = False, ""

        if succes:
            nom = email.split('@')[0]

            # 3. Déploiement VM via OpenTofu
            tofu_ok, ip_vm = deployer_vm_tofu(nom, email, uid, gid)
            if not tofu_ok:
                logger.error(f"Tofu échoué : {ip_vm}")
                rollback_utilisateur(nom)          # nettoie LDAP + Kerberos
                return render_template(
                    'signup.html',
                    error="Échec création VM – ré-essayez plus tard"
                )

            # 4. Configuration post-install (Ansible)
            #inventory_path = f"/srv/ansible/inventories/vm-{nom}.ini"
            #ansible_ok, ansible_msg = lancer_ansible(nom, email, uid, gid, inventory_path)
            #if not ansible_ok:
                #logger.warning(f"Ansible échoué : {ansible_msg}")

            # 5. Notification par mail avec VM ID et IP
            vmid = get_vmid_from_uid(uid)
            envoyer_mail_etudiant(email, nom, vmid=vmid, ip_vm=ip_vm)

            # 6. Page de succès
            return render_template(
                'signup_success.html',
                message=message,
                email=email
            )

        # Échec LDAP
        return render_template('signup.html', error=message)

    # GET → affichage formulaire
    return render_template('signup.html')

@app.route('/guest', methods=['GET', 'POST'])
def guest():
    if request.method == 'POST':
        email             = request.form.get('email')
        mot_de_passe_temp = str(uuid.uuid4())[:8]

        succes, message, uid, gid = creer_utilisateur_ldap(
            email, mot_de_passe_temp, groupe="guests", duree_jours=DUREE_GUEST
        )

        if succes:
            nom = email.split('@')[0]
            envoyer_mail_guest(email, nom, mot_de_passe_temp)
            return render_template('guest_success.html',
                                   message=message,
                                   login=nom,
                                   password=mot_de_passe_temp)
        else:
            return render_template('guest.html', error=message)

    return render_template('guest.html')


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
