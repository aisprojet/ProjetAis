output "vm_ip" {
  value = proxmox_vm_qemu.student_vm.default_ipv4_address
}


output "vm_id" {
  value = proxmox_vm_qemu.student_vm.vmid
}



#output "inventory_path" {
#  value = "/srv/ansible/inventories/${var.vm_name}.ini"
#}
#output "inventory_path" {
 # value = local_file.inventory.filename
#}
