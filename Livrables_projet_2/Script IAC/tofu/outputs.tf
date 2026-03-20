output "vm_ip" {
  value = proxmox_vm_qemu.student_vm.default_ipv4_address
}


output "vm_id" {
  value = proxmox_vm_qemu.student_vm.vmid
}



