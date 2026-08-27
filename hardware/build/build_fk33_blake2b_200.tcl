set build_dir [file normalize [lindex $argv 0]]
set reference_dcp [file normalize [lindex $argv 1]]
set output_dir [file join $build_dir output]
file mkdir $output_dir

set_param general.maxThreads 8

puts "============================================================"
puts "BEGIN FK33 BLAKE2B PROFILE0 FIVE-LANE 200 MHZ FULL BUILD"
puts "BUILD: $build_dir"
puts "REFERENCE: $reference_dcp"
puts "============================================================"

read_verilog -sv [file join $build_dir blake2b_unrolled48.sv]
read_verilog -sv [file join $build_dir blake2b_profile0_fivelane_lean.sv]
read_verilog -sv [file join $build_dir blake2b_profile0_fivelane_controller.sv]
read_verilog -sv [file join $build_dir fk33_blake2b_bscan_transport.sv]
read_verilog -sv [file join $build_dir miner_top_blake2b_profile0_bscan_200.sv]
read_xdc [file join $build_dir fk33_blake2b_200.xdc]

synth_design \
    -top miner_top_blake2b_profile0_bscan_200 \
    -part xcvu33p-fsvh2104-2-e \
    -flatten_hierarchy rebuilt

puts "SYNTHESIS STATUS: COMPLETE"

report_utilization \
    -file [file join $output_dir post-synth-utilization.rpt]

write_checkpoint -force \
    [file join $output_dir post-synth.dcp]

opt_design
puts "OPTIMIZATION STATUS: COMPLETE"

set incremental_rc [catch {
    read_checkpoint -incremental $reference_dcp
} incremental_message]

if {$incremental_rc == 0} {
    puts "INCREMENTAL REFERENCE: ACCEPTED"
} else {
    puts "INCREMENTAL REFERENCE: SKIPPED"
    puts "INCREMENTAL MESSAGE: $incremental_message"
}

place_design -directive AltSpreadLogic_high
puts "PLACEMENT STATUS: COMPLETE"

catch {
    report_incremental_reuse \
        -file [file join $output_dir incremental-reuse.rpt]
} reuse_message

report_design_analysis \
    -congestion \
    -file [file join $output_dir placed-congestion.rpt]

write_checkpoint -force \
    [file join $output_dir placed.dcp]

phys_opt_design -directive AggressiveExplore
puts "PRE-ROUTE PHYSICAL OPTIMIZATION: COMPLETE"

set route_rc [catch {
    route_design -directive Explore
} route_message]

if {$route_rc != 0} {
    puts "ROUTING STATUS: FAILED"
    puts "ROUTING ERROR: $route_message"

    report_route_status \
        -file [file join $output_dir failed-route-status.rpt]

    report_design_analysis \
        -congestion \
        -file [file join $output_dir failed-route-congestion.rpt]

    write_checkpoint -force \
        [file join $output_dir failed-partial-route.dcp]

    close_design
    exit 1
}

puts "ROUTING STATUS: COMPLETE"

phys_opt_design -directive AggressiveExplore
puts "POST-ROUTE PHYSICAL OPTIMIZATION: COMPLETE"

report_timing_summary \
    -delay_type min_max \
    -max_paths 100 \
    -file [file join $output_dir timing_routed.rpt]

report_route_status \
    -file [file join $output_dir route_status.rpt]

report_drc \
    -file [file join $output_dir drc_routed.rpt]

report_utilization \
    -file [file join $output_dir utilization_routed.rpt]

report_design_analysis \
    -congestion \
    -file [file join $output_dir congestion_routed.rpt]

set setup_path [lindex [get_timing_paths \
    -quiet -delay_type max -max_paths 1 -nworst 1] 0]

set hold_path [lindex [get_timing_paths \
    -quiet -delay_type min -max_paths 1 -nworst 1] 0]

if {$setup_path eq "" || $hold_path eq ""} {
    error "TIMING GATE FAILED: timing paths unavailable"
}

set setup_slack [get_property SLACK $setup_path]
set hold_slack [get_property SLACK $hold_path]
set data_delay [get_property DATAPATH_DELAY $setup_path]

puts "FINAL SETUP WNS: $setup_slack ns"
puts "FINAL HOLD WHS: $hold_slack ns"
puts "CRITICAL DATAPATH DELAY: $data_delay ns"

if {$setup_slack < 0.0} {
    error "TIMING GATE FAILED: negative setup slack"
}

if {$hold_slack < 0.0} {
    error "TIMING GATE FAILED: negative hold slack"
}

set unrouted_nets [get_nets -quiet -hierarchical \
    -filter {ROUTE_STATUS == UNROUTED}]
if {[llength $unrouted_nets] != 0} {
    error "ROUTE GATE FAILED: explicitly unrouted nets remain"
}

set drc_errors [get_drc_violations -quiet -filter {SEVERITY == Error}]
puts "FINAL DRC ERROR COUNT: [llength $drc_errors]"

if {[llength $drc_errors] != 0} {
    error "DRC GATE FAILED: error-severity violations remain"
}

puts "TIMING GATE PASS"
puts "ROUTE GATE PASS"
puts "DRC GATE PASS"

set output_dcp [file join $output_dir fk33_blake2b_profile0_5lane_200_routed.dcp]
write_checkpoint -force $output_dcp

set_property BITSTREAM.GENERAL.COMPRESS FALSE [current_design]

set output_bit [file join $output_dir fk33_blake2b_profile0_5lane_200.bit]
write_bitstream -force $output_bit

puts "============================================================"
puts "FK33 BLAKE2B PROFILE0 FIVE-LANE 200 MHZ FULL BUILD COMPLETE"
puts "NOMINAL RATE: 1000 MH/S"
puts "CHECKPOINT: $output_dcp"
puts "BITSTREAM: $output_bit"
puts "============================================================"

close_design
exit
