#!/bin/bash
NOM=$1
VARS_FILE="/srv/tofu/vars/${NOM}.tfvars.json"
VM_DIR="/srv/tofu/vms/${NOM}"
LOG_DIR="/srv/tofu/logs"
LOG_FILE="${LOG_DIR}/${NOM}.log"
export TF_PLUGIN_CACHE_DIR="/var/www/.terraform.d/plugin-cache"

mkdir -p "$VM_DIR"
mkdir -p "$LOG_DIR"

# Initialise le fichier de log avec timestamp
echo "========================================" >> "$LOG_FILE"
echo "Déploiement VM: ${NOM}" >> "$LOG_FILE"
echo "Date: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

cp /srv/tofu/main.tf "$VM_DIR"/
cp /srv/tofu/outputs.tf "$VM_DIR"/
cp /srv/tofu/provider.tf "$VM_DIR"/
cp /srv/tofu/variables.tf "$VM_DIR"/
cp /srv/tofu/terraform.tfvars "$VM_DIR"/


cd "$VM_DIR" || exit 1

echo ">>> tofu init" >> "$LOG_FILE"
tofu init -input=false >> "$LOG_FILE" 2>&1

echo "" >> "$LOG_FILE"
echo ">>> tofu apply" >> "$LOG_FILE"
tofu apply -auto-approve -var-file="$VARS_FILE" >> "$LOG_FILE" 2>&1

# Récupère l'IP et crée l'inventaire
echo "" >> "$LOG_FILE"
echo ">>> Récupération IP" >> "$LOG_FILE"
IP=$(tofu output -raw vm_ip)
echo "IP: ${IP}" >> "$LOG_FILE"

mkdir -p /srv/ansible/inventories
cat > /srv/ansible/inventories/vm-${NOM}.ini << EOF
[vdi]
vm-${NOM} ansible_host=${IP}
[vdi:vars]
ansible_user=ais
ansible_ssh_private_key_file=/var/lib/www-data/.ssh/id_rsa
ansible_ssh_common_args='-o StrictHostKeyChecking=no'
ansible_python_interpreter=/usr/bin/python3
nfs_server=172.16.0.111
ldap_server=172.16.0.118
kerberos_server=172.16.0.118
dns_server=172.16.0.116
domain=techvault.fr
ldap_base_dn=dc=techvault,dc=fr
kerberos_realm=TECHVAULT.FR
EOF

# Attend que la VM soit accessible en SSH
echo "" >> "$LOG_FILE"
echo ">>> Attente SSH sur ${IP}" >> "$LOG_FILE"
MAX_ATTEMPTS=60
ATTEMPT=0
while [ $ATTEMPT -lt $MAX_ATTEMPTS ]; do
    if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=2 -i /var/lib/www-data/.ssh/id_rsa ais@${IP} "exit" 2>/dev/null; then
        echo "SSH disponible après $((ATTEMPT + 1)) tentatives"
        echo "SSH OK après $((ATTEMPT + 1)) tentatives" >> "$LOG_FILE"
        break
    fi
    ATTEMPT=$((ATTEMPT + 1))
    sleep 2
done

if [ $ATTEMPT -eq $MAX_ATTEMPTS ]; then
    echo "Erreur: SSH non disponible après $MAX_ATTEMPTS tentatives" >> "$LOG_FILE"
    echo "========================================" >> "$LOG_FILE"
    exit 1
fi

# Lance Ansible en arrière-plan
echo "" >> "$LOG_FILE"
echo ">>> Lancement Ansible" >> "$LOG_FILE"
echo "Logs Ansible: /srv/ansible/logs/ansible-${NOM}.log" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"
nohup ansible-playbook \
    -i /srv/ansible/inventories/vm-${NOM}.ini \
    /srv/ansible/playbook.yml \
    -e "{\"vm_user\":\"${NOM}\"}" \
    >> /srv/ansible/logs/ansible-${NOM}.log 2>&1 &

echo $IP
