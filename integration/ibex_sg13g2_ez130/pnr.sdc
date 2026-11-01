# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
# LibreLane's PNR reader evaluates Tcl. Keep the common constraints byte-exact
# and establish the same CPPR policy in each optimizing OpenROAD process.
source [file join [file dirname [info script]] constraints.sdc]
source [file join [file dirname [info script]] cppr_off.tcl]
