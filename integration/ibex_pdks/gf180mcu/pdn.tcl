# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
# Public OpenROAD PDN command interface. Geometry is in micrometres.
# The 7-track cells' rails are on Metal1 (pins VDD, VSS; wells VNW, VPW). The foundry SRAM's supply pins are
# horizontal Metal3 straps; Metal4 and Metal5 are free above it. Strap widths, spacings, pitches and offsets of
# the core grid are the PDK's LibreLane defaults for gf180mcuD.
add_global_connection -net VDD -inst_pattern {.*} -pin_pattern {^VDD$} -power
add_global_connection -net VDD -inst_pattern {.*} -pin_pattern {^VNW$} -power
add_global_connection -net VSS -inst_pattern {.*} -pin_pattern {^VSS$} -ground
add_global_connection -net VSS -inst_pattern {.*} -pin_pattern {^VPW$} -ground
global_connect
set_voltage_domain -name CORE -power VDD -ground VSS

define_pdn_grid -name CoreGrid -voltage_domains CORE -pins {Metal5}
add_pdn_stripe -grid CoreGrid -layer Metal1 -width 0.6 -followpins
add_pdn_stripe -grid CoreGrid -layer Metal4 -width 1.6 -spacing 1.7 -pitch 153.6 -offset 16.32
add_pdn_stripe -grid CoreGrid -layer Metal5 -width 1.6 -spacing 1.7 -pitch 153.18 -offset 16.65
add_pdn_connect -grid CoreGrid -layers {Metal1 Metal4}
add_pdn_connect -grid CoreGrid -layers {Metal4 Metal5}

define_pdn_grid -name SramGrid -macro -instances {u_imem0 u_imem1 u_imem2 u_imem3 u_dmem0 u_dmem1 u_dmem2 u_dmem3} \
    -voltage_domains CORE -grid_over_boundary -halo 10
add_pdn_stripe -grid SramGrid -layer Metal4 -width 1.6 -spacing 1.7 -pitch 38.4 -offset 8.0
add_pdn_connect -grid SramGrid -layers {Metal3 Metal4}
add_pdn_connect -grid SramGrid -layers {Metal4 Metal5}
