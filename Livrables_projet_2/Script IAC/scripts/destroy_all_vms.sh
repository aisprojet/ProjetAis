#!/bin/bash
# Destruction des VMs utilisateurs (vmid >= 10000)
# Script à exécuter manuellement ou via cron

LOG_FILE="/var/log/flask/vm_cleanup.log"
PM_API="https://192.168.1.200:8006/api2/json"
PM_TOKEN="Administrateur@pve!NEWTOKEN=3cf465e6-3981-4909-bf60-1bbbcb402ed5"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> "$LOG_FILE"
}

log "=== Début du nettoyage des VMs (vmid >= 10000) ==="

# Récupérer toutes les VMs avec vmid >= 10000
VMS=$(curl -s -k -H "Authorization: PVEAPIToken=${PM_TOKEN}" \
    "${PM_API}/nodes/pve/qemu" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for vm in data['data']:
        vmid = int(vm['vmid'])
        if vmid >= 10000:
            print(f\"{vmid} {vm.get('name', 'unknown')}\")
except:
    pass
" 2>/dev/null)

if [ -z "$VMS" ]; then
    log "Aucune VM à détruire"
    echo "Aucune VM à détruire"
    exit 0
fi

echo "VMs à détruire :"
echo "$VMS"
echo ""
read -p "Confirmer la destruction ? (oui/non) : " CONFIRM

if [ "$CONFIRM" != "oui" ]; then
    echo "Annulé"
    log "Destruction annulée par l'utilisateur"
    exit 0
fi

# Détruire chaque VM
while read -r vmid name; do
    log "Arrêt de la VM $vmid ($name)..."
    echo "Arrêt de la VM $vmid ($name)..."
    curl -s -k -X POST -H "Authorization: PVEAPIToken=${PM_TOKEN}" \
        "${PM_API}/nodes/pve/qemu/${vmid}/status/stop" > /dev/null 2>&1

    # Attendre l'arrêt
    sleep 5

    log "Destruction de la VM $vmid ($name)..."
    echo "Destruction de la VM $vmid ($name)..."
    curl -s -k -X DELETE -H "Authorization: PVEAPIToken=${PM_TOKEN}" \
        "${PM_API}/nodes/pve/qemu/${vmid}?purge=1" > /dev/null 2>&1

    # Supprimer le tfstate correspondant
    USER=$(echo "$name" | sed 's/^vm-//')
    if [ -n "$USER" ]; then
        rm -rf "/srv/tofu/vms/${USER}" 2>/dev/null
        rm -f "/srv/tofu/vars/${USER}.tfvars.json" 2>/dev/null
        log "Tfstate supprimé pour $USER"
    fi
done <<< "$VMS"

log "=== Fin du nettoyage ==="
log "VMs détruites: $(echo "$VMS" | wc -l)"
echo "Terminé ! $(echo "$VMS" | wc -l) VMs détruites."