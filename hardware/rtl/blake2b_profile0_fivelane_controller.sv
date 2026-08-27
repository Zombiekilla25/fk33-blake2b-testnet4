`timescale 1ns/1ps

module blake2b_profile0_fivelane_controller (
    input  logic         clk,
    input  logic         rst,

    input  logic         job_pulse,
    input  logic [7:0]   job_tag,
    input  logic [639:0] job_asic_input,
    input  logic [255:0] job_target_numeric,

    input  logic         share_ready,
    output logic         share_valid,
    output logic [7:0]   share_tag,
    output logic [31:0]  share_nonce,
    output logic [255:0] share_digest
);

    wire [255:0] job_prevblock_hidden = job_asic_input[0 +: 256];
    wire [31:0]  job_start_nonce      = job_asic_input[256 +: 32];
    wire [31:0]  job_nonce2           = job_asic_input[288 +: 32];
    wire [31:0]  job_time_offset      = job_asic_input[320 +: 32];
    wire [31:0]  job_nonce3           = job_asic_input[352 +: 32];
    wire [255:0] job_prehash          = job_asic_input[384 +: 256];

    wire [4:0] result_valid;
    wire [159:0] result_nonce;
    wire [4:0] result_meets_target;

    logic candidate_pending = 1'b0;
    logic selected_hit;
    logic [31:0] selected_nonce;

    // Keep issuing nonces while the one-entry response mailbox is occupied.
    // Hits produced while candidate_pending is set are intentionally dropped;
    // this prevents a stalled pipeline result from being returned twice.
    wire miner_enable = !rst && !job_pulse;

    blake2b_profile0_fivelane_lean u_miner (
        .clk(clk),
        .rst(rst),
        .job_load(job_pulse),
        .enable(miner_enable),
        .job_prevblock_hidden(job_prevblock_hidden),
        .job_start_nonce(job_start_nonce),
        .job_nonce2(job_nonce2),
        .job_time_offset(job_time_offset),
        .job_nonce3(job_nonce3),
        .job_prehash(job_prehash),
        // Sia-class Profile-0 compares the raw BLAKE2b digest. DATUM
        // performs the consensus XOR-mask transformation on submission.
        .job_xor_mask_bytes(256'd0),
        .job_target_numeric(job_target_numeric),
        .result_valid(result_valid),
        .result_nonce(result_nonce),
        .result_meets_target(result_meets_target)
    );

    always_comb begin
        selected_hit = 1'b0;
        selected_nonce = 32'd0;

        if (result_valid[0] && result_meets_target[0]) begin
            selected_hit = 1'b1;
            selected_nonce = result_nonce[0 +: 32];
        end else if (result_valid[1] && result_meets_target[1]) begin
            selected_hit = 1'b1;
            selected_nonce = result_nonce[32 +: 32];
        end else if (result_valid[2] && result_meets_target[2]) begin
            selected_hit = 1'b1;
            selected_nonce = result_nonce[64 +: 32];
        end else if (result_valid[3] && result_meets_target[3]) begin
            selected_hit = 1'b1;
            selected_nonce = result_nonce[96 +: 32];
        end else if (result_valid[4] && result_meets_target[4]) begin
            selected_hit = 1'b1;
            selected_nonce = result_nonce[128 +: 32];
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            candidate_pending <= 1'b0;
            share_valid <= 1'b0;
            share_tag <= 8'd0;
            share_nonce <= 32'd0;
            share_digest <= 256'd0;
        end else begin
            share_valid <= 1'b0;

            if (job_pulse) begin
                candidate_pending <= 1'b0;
            end else if (candidate_pending) begin
                if (share_ready) begin
                    share_valid <= 1'b1;
                    candidate_pending <= 1'b0;
                end
            end else if (selected_hit) begin
                share_tag <= job_tag;
                share_nonce <= selected_nonce;
                // The lean routed core intentionally removes the 1280-bit
                // digest output network. The host recomputes and verifies it.
                share_digest <= 256'd0;
                candidate_pending <= 1'b1;
            end
        end
    end

endmodule
