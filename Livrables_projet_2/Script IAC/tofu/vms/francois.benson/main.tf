resource "proxmox_vm_qemu" "student_vm" {
  vmid        = 2000 + tonumber(var.vm_uid)
  name        = var.vm_name
  target_node = var.node
  clone       = var.modele
  full_clone  = true
  memory      = 4096
  scsihw      = "virtio-scsi-pci"
  bootdisk    = "scsi0"
  agent       = 1
  agent_timeout = 120
  boot        = "order=scsi0"
  skip_ipv6   = true
  ipconfig0 = "ip=dhcp"
  ciuser    = var.vm_user

  cpu {
    cores   = 2
    sockets = 1
    type    = "host"
  }

  vga {
    type   = "qxl"
    memory = 64
  }

  disk {
    slot    = "scsi0"
    type    = "disk"
    storage = var.storage
    size    = "32G"
    discard = true
}
  network {
    id     = 0
    model  = "virtio"
    bridge = "vmbr1"
    tag    = 5
  }

#  cicustom = "user=local:snippets/${var.vm_user}-user.yaml"

  lifecycle {
    ignore_changes = [network, cipassword, disk, bootdisk]
  }
}

#resource "local_file" "ansible_inventory" {
 # content = templatefile("/srv/ansible/inventory.tpl", {
  #  vm_name = proxmox_vm_qemu.student_vm.name
   # vm_ip   = proxmox_vm_qemu.student_vm.default_ipv4_address
  #})
  #filename = "/srv/ansible/inventory.ini"
#}

#resource "local_file" "ansible_inventory" {
 # content = templatefile("/srv/ansible/inventory.tpl", {
  #  vm_name = proxmox_vm_qemu.student_vm.name
   # vm_ip   = proxmox_vm_qemu.student_vm.default_ipv4_address
  #})
  #filename = "/srv/ansible/inventories/${var.vm_name}.ini"
#}

