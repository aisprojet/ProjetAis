variable "api" {
  type    = string
  default = "https://172.16.10.10:8006/api2/json"
}

variable "token_id" {
  type     = string
  sensitive = true
}

variable "token_secret" {
  type     = string
  sensitive = true
}

variable "tls" {
  type    = bool
  default = true
}

variable "node" {
  type    = string
  default = "pve"
}

variable "storage" {
  type    = string
  default = "local-lvm"
}

variable "modele" {
  description = "Modele-Debian-cloudinit"
  type        = string
  default     = "200"
}


variable "nombre" {
  type    = number
  default = 1
}
variable "vm_name" { type = string }
variable "vm_user" { type = string }
variable "vm_uid"  { type = string }
variable "vm_gid"  { type = string }
