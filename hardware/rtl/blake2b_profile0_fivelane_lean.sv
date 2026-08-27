`timescale 1ns/1ps

/*
 * Lean five-lane Testnet4 profile-0 miner.
 *
 * Only candidate-valid, nonce and target-hit results leave this core.
 * Full digests remain local to eliminate the previous 1280-bit diagnostic
 * output network.
 *
 * Core instance names deliberately match the qualified routed reference:
 *   u_core_0 through u_core_4
 */
module blake2b_profile0_fivelane_lean (
    input  logic         clk,
    input  logic         rst,
    input  logic         job_load,
    input  logic         enable,

    input  logic [255:0] job_prevblock_hidden,
    input  logic [31:0]  job_start_nonce,
    input  logic [31:0]  job_nonce2,
    input  logic [31:0]  job_time_offset,
    input  logic [31:0]  job_nonce3,
    input  logic [255:0] job_prehash,
    input  logic [255:0] job_xor_mask_bytes,
    input  logic [255:0] job_target_numeric,

    output logic [4:0]   result_valid,
    output logic [159:0] result_nonce,
    output logic [4:0]   result_meets_target
);

    localparam integer LANES = 5;
    localparam integer CORE_LATENCY = 48;

    logic loaded;
    logic [31:0] nonce_base;
    logic [31:0] nonce_base_pipe [0:CORE_LATENCY];

    logic [255:0] prevblock_lane [0:LANES-1];
    logic [31:0]  nonce2_lane [0:LANES-1];
    logic [31:0]  time_offset_lane [0:LANES-1];
    logic [31:0]  nonce3_lane [0:LANES-1];
    logic [255:0] prehash_lane [0:LANES-1];
    logic [255:0] xor_mask_lane [0:LANES-1];
    logic [255:0] target_lane [0:LANES-1];

    logic [7:0] message_bytes_lane [0:LANES-1];
    logic [6:0] digest_bytes_lane [0:LANES-1];

    logic [31:0] lane_nonce [0:LANES-1];
    logic [1023:0] lane_block [0:LANES-1];

    logic [4:0] core_valid_out;
    logic [511:0] core_digest [0:LANES-1];

    (* MAX_FANOUT = 64 *)
    logic issue_valid;

    (* MAX_FANOUT = 64 *)
    logic lane_reset;

    integer index;

    function automatic logic [255:0] reverse_bytes_256 (
        input logic [255:0] value
    );
        integer byte_index;
        begin
            for (
                byte_index = 0;
                byte_index < 32;
                byte_index = byte_index + 1
            )
                reverse_bytes_256[8*byte_index +: 8] =
                    value[255 - 8*byte_index -: 8];
        end
    endfunction

    assign issue_valid = loaded && enable && !job_load;
    assign lane_reset = rst | job_load;

    always_ff @(posedge clk) begin
        if (rst) begin
            loaded <= 1'b0;
            nonce_base <= 32'd0;

            for (
                index = 0;
                index <= CORE_LATENCY;
                index = index + 1
            )
                nonce_base_pipe[index] <= 32'd0;

            for (
                index = 0;
                index < LANES;
                index = index + 1
            ) begin
                prevblock_lane[index] <= 256'd0;
                nonce2_lane[index] <= 32'd0;
                time_offset_lane[index] <= 32'd0;
                nonce3_lane[index] <= 32'd0;
                prehash_lane[index] <= 256'd0;
                xor_mask_lane[index] <= 256'd0;
                target_lane[index] <= 256'd0;
                message_bytes_lane[index] <= 8'd0;
                digest_bytes_lane[index] <= 7'd0;
            end
        end else begin
            if (job_load) begin
                loaded <= 1'b1;
                nonce_base <= job_start_nonce;

                for (
                    index = 0;
                    index < LANES;
                    index = index + 1
                ) begin
                    prevblock_lane[index] <=
                        job_prevblock_hidden;

                    nonce2_lane[index] <= job_nonce2;
                    time_offset_lane[index] <= job_time_offset;
                    nonce3_lane[index] <= job_nonce3;
                    prehash_lane[index] <= job_prehash;
                    xor_mask_lane[index] <= job_xor_mask_bytes;
                    target_lane[index] <= job_target_numeric;

                    message_bytes_lane[index] <= 8'd80;
                    digest_bytes_lane[index] <= 7'd32;
                end
            end else if (issue_valid) begin
                nonce_base <= nonce_base + 32'd5;
            end

            if (job_load) begin
                for (
                    index = 0;
                    index <= CORE_LATENCY;
                    index = index + 1
                )
                    nonce_base_pipe[index] <= 32'd0;
            end else begin
                nonce_base_pipe[0] <= nonce_base;

                for (
                    index = 1;
                    index <= CORE_LATENCY;
                    index = index + 1
                )
                    nonce_base_pipe[index] <=
                        nonce_base_pipe[index - 1];
            end
        end
    end

    always_comb begin
        for (
            integer lane = 0;
            lane < LANES;
            lane = lane + 1
        ) begin
            lane_nonce[lane] = nonce_base + lane;
            lane_block[lane] = 1024'd0;

            lane_block[lane][0 +: 256] =
                prevblock_lane[lane];

            // Profile 0 hides the first six serialized bytes.
            lane_block[lane][0 +: 48] = 48'd0;

            lane_block[lane][256 +: 32] =
                lane_nonce[lane];

            lane_block[lane][288 +: 32] =
                nonce2_lane[lane];

            lane_block[lane][320 +: 32] =
                time_offset_lane[lane];

            lane_block[lane][352 +: 32] =
                nonce3_lane[lane];

            lane_block[lane][384 +: 256] =
                prehash_lane[lane];
        end
    end

    (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "yes" *)
    blake2b_unrolled48 u_core_0 (
        .clk(clk),
        .rst(lane_reset),
        .valid_in(issue_valid),
        .block_in(lane_block[0]),
        .message_bytes(message_bytes_lane[0]),
        .digest_bytes(digest_bytes_lane[0]),
        .valid_out(core_valid_out[0]),
        .digest_out(core_digest[0])
    );

    (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "yes" *)
    blake2b_unrolled48 u_core_1 (
        .clk(clk),
        .rst(lane_reset),
        .valid_in(issue_valid),
        .block_in(lane_block[1]),
        .message_bytes(message_bytes_lane[1]),
        .digest_bytes(digest_bytes_lane[1]),
        .valid_out(core_valid_out[1]),
        .digest_out(core_digest[1])
    );

    (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "yes" *)
    blake2b_unrolled48 u_core_2 (
        .clk(clk),
        .rst(lane_reset),
        .valid_in(issue_valid),
        .block_in(lane_block[2]),
        .message_bytes(message_bytes_lane[2]),
        .digest_bytes(digest_bytes_lane[2]),
        .valid_out(core_valid_out[2]),
        .digest_out(core_digest[2])
    );

    (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "yes" *)
    blake2b_unrolled48 u_core_3 (
        .clk(clk),
        .rst(lane_reset),
        .valid_in(issue_valid),
        .block_in(lane_block[3]),
        .message_bytes(message_bytes_lane[3]),
        .digest_bytes(digest_bytes_lane[3]),
        .valid_out(core_valid_out[3]),
        .digest_out(core_digest[3])
    );

    (* KEEP_HIERARCHY = "yes", DONT_TOUCH = "yes" *)
    blake2b_unrolled48 u_core_4 (
        .clk(clk),
        .rst(lane_reset),
        .valid_in(issue_valid),
        .block_in(lane_block[4]),
        .message_bytes(message_bytes_lane[4]),
        .digest_bytes(digest_bytes_lane[4]),
        .valid_out(core_valid_out[4]),
        .digest_out(core_digest[4])
    );

    generate
        for (
            genvar lane = 0;
            lane < LANES;
            lane = lane + 1
        ) begin : g_result

            localparam logic [31:0] LANE_OFFSET = lane;

            logic [255:0] numeric_hash_wire;

            logic valid_s0;
            logic [31:0] nonce_s0;
            logic [255:0] hash_s0;

            logic valid_s1;
            logic [31:0] nonce_s1;
            logic [3:0] less_s1;
            logic [3:0] equal_s1;

            logic valid_s2;
            logic [31:0] nonce_s2;
            logic hit_s2;

            assign numeric_hash_wire =
                reverse_bytes_256(
                    core_digest[lane][255:0] ^
                    xor_mask_lane[lane]
                );

            always_ff @(posedge clk) begin
                if (lane_reset) begin
                    valid_s0 <= 1'b0;
                    nonce_s0 <= 32'd0;
                    hash_s0 <= 256'd0;

                    valid_s1 <= 1'b0;
                    nonce_s1 <= 32'd0;
                    less_s1 <= 4'd0;
                    equal_s1 <= 4'd0;

                    valid_s2 <= 1'b0;
                    nonce_s2 <= 32'd0;
                    hit_s2 <= 1'b0;
                end else begin
                    valid_s0 <= core_valid_out[lane];

                    if (core_valid_out[lane]) begin
                        nonce_s0 <=
                            nonce_base_pipe[CORE_LATENCY] +
                            LANE_OFFSET;

                        hash_s0 <= numeric_hash_wire;
                    end

                    valid_s1 <= valid_s0;

                    if (valid_s0) begin
                        nonce_s1 <= nonce_s0;

                        less_s1[3] <=
                            hash_s0[255:192] <
                            target_lane[lane][255:192];

                        equal_s1[3] <=
                            hash_s0[255:192] ==
                            target_lane[lane][255:192];

                        less_s1[2] <=
                            hash_s0[191:128] <
                            target_lane[lane][191:128];

                        equal_s1[2] <=
                            hash_s0[191:128] ==
                            target_lane[lane][191:128];

                        less_s1[1] <=
                            hash_s0[127:64] <
                            target_lane[lane][127:64];

                        equal_s1[1] <=
                            hash_s0[127:64] ==
                            target_lane[lane][127:64];

                        less_s1[0] <=
                            hash_s0[63:0] <
                            target_lane[lane][63:0];

                        equal_s1[0] <=
                            hash_s0[63:0] ==
                            target_lane[lane][63:0];
                    end

                    valid_s2 <= valid_s1;

                    if (valid_s1) begin
                        nonce_s2 <= nonce_s1;

                        hit_s2 <=
                            less_s1[3] ||
                            (
                                equal_s1[3] &&
                                (
                                    less_s1[2] ||
                                    (
                                        equal_s1[2] &&
                                        (
                                            less_s1[1] ||
                                            (
                                                equal_s1[1] &&
                                                (
                                                    less_s1[0] ||
                                                    equal_s1[0]
                                                )
                                            )
                                        )
                                    )
                                )
                            );
                    end
                end
            end

            assign result_valid[lane] = valid_s2;
            assign result_nonce[32*lane +: 32] = nonce_s2;

            assign result_meets_target[lane] =
                valid_s2 && hit_s2;
        end
    endgenerate

endmodule
