# TechVault — Infrastructure VDI & Gestion Centralisée

Projet certifiant RNCP37680 — Administrateur d'Infrastructures Sécurisées
Laser-Campus Numérique

---

## Description

TechVault est une infrastructure IT automatisée permettant :
- La gestion centralisée des utilisateurs via OpenLDAP et Kerberos
- L'accès sécurisé aux ressources partagées (NFS)
- Le provisionnement automatique de postes de travail virtuels (VDI)
- Une interface Web de gestion (Flask + Nginx HTTPS)

---

## Architecture

| Serveur         | IP            | Rôle                          |
|-----------------|---------------|-------------------------------|
| Proxmox VE      | 192.168.1.200 | Hyperviseur bare metal        |
| LDAP / Kerberos | 172.16.0.118  | Annuaire + authentification   |
| NFS             | 172.16.0.111  | Stockage privé et commun      |
| DNS             | 172.16.0.116  | Résolution de noms dynamique  |
| Flask / Nginx   | 172.16.0.x    | Interface Web HTTPS           |

---

## Prérequis

- Proxmox VE 9.x installé en bare metal
- OpenTofu >= 1.6
- Ansible >= 2.15
- Python 3.x + pip
- Accès SSH avec clé RSA depuis le serveur Flask

---

## Structure du dépôt
techvault/
├── opentofu/ # Provisionnement des VMs (OpenTofu)
│ ├── main.tf
│ ├── variables.tf
│ ├── provider.tf
│ └── outputs.tf
├── ansible/ # Configuration post-déploiement
│ ├── ansible.cfg
│ ├── inventory.tpl
│ ├── playbook.yml
│ └── roles/
├── flask/ # Interface Web
│ ├── app.py
│ ├── templates/
│ └── requirements.txt
└── README.md

Sécurité

Authentification centralisée LDAP + Kerberos

Communications chiffrées HTTPS (Nginx)

SSH par clé uniquement (PasswordAuthentication désactivé)

Comptes invités avec expiration automatique (7 jours)

Supervision des VMs via agent Wazuh

Sauvegarde

Les VMs sont sauvegardées quotidiennement via Proxmox Backup Server (PBS).
Consulter le PRI (Plan de Reprise Informatique) dans la documentation technique
pour les procédures de restauration.

Auteurs
Alexandru Nasui (Stagiaire 1) — Infrastructure (Proxmox, LDAP, NFS, Kerberos, Sauvegardes)

Benson François (Stagiaire 2) — Automatisation (Flask, OpenTofu, Ansible)

Licence
Projet réalisé dans le cadre d'un stage — Laser-Campus Numérique — 2026