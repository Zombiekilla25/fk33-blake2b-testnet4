`timescale 1ns/1ps

module tb_blake2b_profile0_fivelane_controller;

    logic clk = 1'b0;
    logic rst = 1'b1;
    logic job_pulse = 1'b0;
    logic [7:0] job_tag = 8'd0;
    logic [639:0] job_asic_input = 640'd0;
    logic [255:0] job_target_numeric = 256'd0;
    logic share_ready = 1'b0;

    wire share_valid;
    wire [7:0] share_tag;
    wire [31:0] share_nonce;
    wire [255:0] share_digest;

    integer failures = 0;
    integer cycles;

    always #2.5 clk = ~clk;

    function automatic [639:0] serialized_bytes80 (
        input logic [639:0] displayed_hex
    );
        integer byte_index;
        begin
            for (byte_index = 0; byte_index < 80; byte_index = byte_index + 1)
                serialized_bytes80[8*byte_index +: 8] =
                    displayed_hex[639 - 8*byte_index -: 8];
        end
    endfunction

    task automatic load_job (
        input logic [7:0] tag,
        input logic [639:0] displayed_asic
    );
        begin
            @(negedge clk);
            job_tag = tag;
            job_asic_input = serialized_bytes80(displayed_asic);
            job_target_numeric = 256'hffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff;
            job_pulse = 1'b1;
            @(negedge clk);
            job_pulse = 1'b0;
        end
    endtask

    task automatic wait_share (
        input logic [7:0] expected_tag,
        input logic [31:0] expected_nonce
    );
        begin
            cycles = 0;
            while (!share_valid && cycles < 100) begin
                @(negedge clk);
                cycles = cycles + 1;
            end

            if (!share_valid) begin
                $display("FAIL: share timeout tag=%02x", expected_tag);
                failures = failures + 1;
            end else begin
                $display(
                    "SHARE tag=%02x nonce=%08x latency=%0d digest=%064x",
                    share_tag,
                    share_nonce,
                    cycles,
                    share_digest
                );

                if (share_tag !== expected_tag) begin
                    $display("FAIL: share tag mismatch");
                    failures = failures + 1;
                end

                if (share_nonce !== expected_nonce) begin
                    $display(
                        "FAIL: nonce mismatch expected=%08x received=%08x",
                        expected_nonce,
                        share_nonce
                    );
                    failures = failures + 1;
                end

                if (share_digest !== 256'd0) begin
                    $display("FAIL: lean response digest is not zero");
                    failures = failures + 1;
                end
            end
        end
    endtask

    blake2b_profile0_fivelane_controller dut (
        .clk(clk),
        .rst(rst),
        .job_pulse(job_pulse),
        .job_tag(job_tag),
        .job_asic_input(job_asic_input),
        .job_target_numeric(job_target_numeric),
        .share_ready(share_ready),
        .share_valid(share_valid),
        .share_tag(share_tag),
        .share_nonce(share_nonce),
        .share_digest(share_digest)
    );

    initial begin
        repeat (8) @(negedge clk);
        rst = 1'b0;

        // Official RC2 Profile-0 vector. Candidate nonce bytes at ASIC
        // offsets 32..35 are 0d f0 ad 0b -> numeric nonce 0x0badf00d.
        load_job(
            8'h10,
            640'h000000000000943aff74219e1f45899abfdf536373c0f2fc92e6fe58335cd0ad0df0ad0b4433221158020000efcdab897e6326906eaa52fe59e03a14f1dfb8dd5d6e78497e56a8a6e4f4fb4d385e43db
        );

        // Exercise response backpressure before releasing the mailbox.
        repeat (65) @(negedge clk);
        if (share_valid) begin
            $display("FAIL: share escaped while share_ready was low");
            failures = failures + 1;
        end

        share_ready = 1'b1;
        wait_share(8'h10, 32'h0badf00d);

        repeat (4) @(negedge clk);

        // Official disabled-selector Profile-0 vector begins at ffffffff.
        load_job(
            8'h90,
            640'h000000000000943aff74219e1f45899abfdf536373c0f2fc92e6fe58335cd0adffffffff4433221188776655efcdab89544a71e01a4c041c727e86ec7cb2c68c62d9dcab0ee9b07cdaf1a59bf2e5d40b
        );

        wait_share(8'h90, 32'hffffffff);

        if (failures != 0)
            $fatal(1, "BOARD CONTROLLER TEST FAILED: %0d error(s)", failures);

        $display("ALL FK33 BLAKE2B BOARD CONTROLLER TESTS PASSED");
        $display("PASS: exact ASIC80 byte mapping");
        $display("PASS: mailbox backpressure and nonce return");
        $finish;
    end

endmodule
