# Original RustLane routing/RCX starter. Real PDK/design execution: UNVERIFIED.
# Requires an explicitly prepared ODB: this script does not create PDN, CTS,
# tap/endcap cells, tie cells, antenna repair or manufacturing fill.
set exit_on_error 1
set file_continue_on_error 0

proc rl_input {key} {
    global rustlane_collateral
    if {![dict exists $rustlane_collateral $key]} { error "RUSTLANE_MISSING_COLLATERAL: $key" }
    set path [dict get $rustlane_collateral $key]
    if {![file isfile $path] || ![file readable $path]} { error "RUSTLANE_UNREADABLE_COLLATERAL: $key" }
    return $path
}
set prepared [rl_input prepared_odb]
set rules [rl_input rcx_rules]
set channel [open [rl_input route_settings] r]
set settings [read $channel 8193]
close $channel
if {[string length $settings] > 8192 || [catch {dict size $settings}]} {
    error "RUSTLANE_INVALID_SETTINGS: expected a Tcl dictionary of at most 8192 characters"
}
set required {signal_layers clock_layers grt_iterations drt_iterations seed extraction_corner}
foreach key $required {
    if {![dict exists $settings $key]} { error "RUSTLANE_MISSING_SETTING: $key" }
}
foreach key [dict keys $settings] {
    if {$key ni $required} { error "RUSTLANE_UNKNOWN_SETTING: $key" }
}
foreach key {signal_layers clock_layers} {
    if {![regexp {^[A-Za-z0-9_]+-[A-Za-z0-9_]+$} [dict get $settings $key]]} {
        error "RUSTLANE_INVALID_SETTINGS: routing range must be an explicit lower-upper layer pair"
    }
}
foreach {key maximum} {grt_iterations 1000 drt_iterations 64 seed 2147483647} {
    set value [dict get $settings $key]
    if {![string is integer -strict $value] || $value < 1 || $value > $maximum} {
        error "RUSTLANE_INVALID_SETTINGS: $key outside finite supported range"
    }
}
if {![regexp {^[A-Za-z0-9_-]+$} [dict get $settings extraction_corner]]} {
    error "RUSTLANE_INVALID_SETTINGS: invalid extraction corner"
}

read_db $prepared
if {[check_placement -verbose] != 0} { error "RUSTLANE_ILLEGAL_PREPARED_PLACEMENT" }
set_routing_layers -signal [dict get $settings signal_layers] -clock [dict get $settings clock_layers]
# Zero critical-net percentage disables the documented timing-priority path.
global_route -critical_nets_percentage 0 \
    -congestion_iterations [dict get $settings grt_iterations] \
    -guide_file [file join $rustlane_output_dir routing.guide]
detailed_route -droute_end_iter [dict get $settings drt_iterations] \
    -or_seed [dict get $settings seed] \
    -output_drc [file join $rustlane_output_dir router-drc.rpt]
define_process_corner -ext_model_index 0 [dict get $settings extraction_corner]
set_extraction_rules_file $rules
extract_parasitics -corner_cnt 1 -corner 0
write_spef [file join $rustlane_output_dir routed.spef]
write_verilog [file join $rustlane_output_dir routed.nl.v]
write_def [file join $rustlane_output_dir routed.def]
write_db [file join $rustlane_output_dir routed.odb]
# Router diagnostics are not a foundry signoff DRC verdict. RustSTA owns STA.
