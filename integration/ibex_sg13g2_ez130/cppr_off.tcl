# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
# Same explicit OCV analysis policy as the native RustSTA run.
if {![info exists ::sta_crpr_enabled]} {
    error "Required global CPPR control is unavailable"
}
set ::sta_crpr_enabled 0
if {$::sta_crpr_enabled != 0} {
    error "Failed to disable CPPR"
}
puts "RUSTEDA_CPPR_OFF enabled=$::sta_crpr_enabled"
