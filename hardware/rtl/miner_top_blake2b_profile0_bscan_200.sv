`timescale 1ns/1ps

module miner_top_blake2b_profile0_bscan_200 (
    input wire clk_p,
    input wire clk_n
);

    wire clk200_raw;
    wire clk200;

    IBUFDS #(
        .DIFF_TERM("TRUE"),
        .IBUF_LOW_PWR("FALSE")
    ) u_ibufds (
        .I(clk_p),
        .IB(clk_n),
        .O(clk200_raw)
    );

    BUFG u_bufg200 (
        .I(clk200_raw),
        .O(clk200)
    );

    logic [7:0] reset_release200 = 8'h00;

    always_ff @(posedge clk200)
        reset_release200 <= {reset_release200[6:0], 1'b1};

    wire reset200 = ~reset_release200[7];

    wire [639:0] bscan_job_asic_input;
    wire [255:0] bscan_job_target;
    wire [7:0] bscan_job_tag;
    wire bscan_job_pulse;
    wire bscan_share_ready;

    wire bscan_share_valid;
    wire [7:0] bscan_share_tag;
    wire [31:0] bscan_share_nonce;
    wire [255:0] bscan_share_digest;

    fk33_bscan_transport u_bscan_transport (
        .clk(clk200),
        .job_asic_input(bscan_job_asic_input),
        .job_target(bscan_job_target),
        .job_tag(bscan_job_tag),
        .job_pulse(bscan_job_pulse),
        .share_valid(bscan_share_valid),
        .share_tag(bscan_share_tag),
        .share_nonce(bscan_share_nonce),
        .share_digest(bscan_share_digest),
        .share_ready(bscan_share_ready)
    );

    blake2b_profile0_fivelane_controller u_controller (
        .clk(clk200),
        .rst(reset200),
        .job_pulse(bscan_job_pulse),
        .job_tag(bscan_job_tag),
        .job_asic_input(bscan_job_asic_input),
        .job_target_numeric(bscan_job_target),
        .share_ready(bscan_share_ready),
        .share_valid(bscan_share_valid),
        .share_tag(bscan_share_tag),
        .share_nonce(bscan_share_nonce),
        .share_digest(bscan_share_digest)
    );

endmodule
