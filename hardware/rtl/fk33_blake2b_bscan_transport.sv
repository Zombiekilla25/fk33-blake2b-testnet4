`timescale 1ns/1ps

// Framed byte transport for the SQRL FK33 raw-JTAG bridge.
//
// USER2 (host -> FPGA):
//   46 4a 01 01 71 00 <113-byte payload> <CRC16 little-endian>
//   payload = tag[1] + 80-byte Profile-0 ASIC input + 32-byte LE target
//
// USER1 (FPGA -> host):
//   46 4a 01 02 25 00 <37-byte payload> <CRC16 little-endian>
//   payload = tag[1] + 4-byte LE nonce + 32 digest bytes
//
// USER3 status bit 2 is one while an outbound byte is available.  This is
// the interface exercised by sqrl_bridge_rawjtag_coe in raw JTAG-UART mode.
// CRC is CRC-16/CCITT-FALSE over the payload only (poly 0x1021, init 0xffff).
module fk33_bscan_transport (
    input  wire         clk,

    output logic [639:0] job_asic_input = '0,
    output logic [255:0] job_target = '0,
    output logic [7:0]   job_tag = '0,
    output logic         job_pulse = 1'b0,

    input  wire          share_valid,
    input  wire [7:0]    share_tag,
    input  wire [31:0]   share_nonce,
    input  wire [255:0]  share_digest,
    output wire          share_ready
);
    localparam integer JOB_PAYLOAD_BYTES = 113;
    localparam integer SHARE_PAYLOAD_BYTES = 37;
    localparam integer SHARE_FRAME_BYTES = 45;

    // At clk=200 MHz this allows two seconds for the bridge to drain
    // one complete 45-byte response before abandoning a wedged frame.
    localparam integer SHARE_PENDING_TIMEOUT_CYCLES = 400000000;

    function automatic [15:0] crc16_byte(
        input [15:0] crc_in,
        input [7:0] data
    );
        reg [15:0] crc;
        integer bit_index;
        begin
            crc = crc_in ^ {data, 8'h00};
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
                if (crc[15])
                    crc = (crc << 1) ^ 16'h1021;
                else
                    crc = crc << 1;
            end
            crc16_byte = crc;
        end
    endfunction

    function automatic [295:0] make_share_payload(
        input [7:0] tag,
        input [31:0] nonce,
        input [255:0] digest
    );
        reg [295:0] payload;
        integer byte_index;
        begin
            payload = '0;
            payload[7:0] = tag;
            payload[8 +: 32] = nonce;

            // Preserve the same digest byte order emitted by the VIO/Tcl
            // bridge: the first wire byte is digest[255:248].
            for (byte_index = 0; byte_index < 32; byte_index = byte_index + 1)
                payload[(5 + byte_index)*8 +: 8] =
                    digest[(31 - byte_index)*8 +: 8];

            make_share_payload = payload;
        end
    endfunction

    function automatic [15:0] crc16_share(input [295:0] payload);
        reg [15:0] crc;
        integer byte_index;
        begin
            crc = 16'hffff;
            for (byte_index = 0; byte_index < SHARE_PAYLOAD_BYTES;
                 byte_index = byte_index + 1)
                crc = crc16_byte(
                    crc,
                    payload[byte_index*8 +: 8]
                );
            crc16_share = crc;
        end
    endfunction

    function automatic [359:0] make_share_frame(
        input [7:0] tag,
        input [31:0] nonce,
        input [255:0] digest
    );
        reg [359:0] frame;
        reg [295:0] payload;
        reg [15:0] crc;
        integer byte_index;
        begin
            payload = make_share_payload(tag, nonce, digest);
            crc = crc16_share(payload);
            frame = '0;
            frame[0*8 +: 8] = 8'h46;
            frame[1*8 +: 8] = 8'h4a;
            frame[2*8 +: 8] = 8'h01;
            frame[3*8 +: 8] = 8'h02;
            frame[4*8 +: 8] = 8'h25;
            frame[5*8 +: 8] = 8'h00;
            for (byte_index = 0; byte_index < SHARE_PAYLOAD_BYTES;
                 byte_index = byte_index + 1)
                frame[(6 + byte_index)*8 +: 8] =
                    payload[byte_index*8 +: 8];
            frame[43*8 +: 8] = crc[7:0];
            frame[44*8 +: 8] = crc[15:8];
            make_share_frame = frame;
        end
    endfunction

    wire capture1, drck1, reset1, runtest1;
    wire sel1, shift1, tck1, tdi1, tms1, update1;
    wire capture2, drck2, reset2, runtest2;
    wire sel2, shift2, tck2, tdi2, tms2, update2;
    wire capture3, drck3, reset3, runtest3;
    wire sel3, shift3, tck3, tdi3, tms3, update3;

    // ============================================================
    // USER2 receive path and job-frame parser (DRCK2 domain).
    // ============================================================
    localparam logic [3:0] RX_MAGIC0  = 4'd0;
    localparam logic [3:0] RX_MAGIC1  = 4'd1;
    localparam logic [3:0] RX_VERSION = 4'd2;
    localparam logic [3:0] RX_TYPE    = 4'd3;
    localparam logic [3:0] RX_LEN0    = 4'd4;
    localparam logic [3:0] RX_LEN1    = 4'd5;
    localparam logic [3:0] RX_PAYLOAD = 4'd6;
    localparam logic [3:0] RX_CRC0    = 4'd7;
    localparam logic [3:0] RX_CRC1    = 4'd8;

    // One USER2 byte occupies eight bits on a single selected FPGA.
    // On the second FPGA in a JCM33 chain, the other FPGA contributes
    // one BYPASS bit, producing a nine-bit transaction.
    logic [8:0] rx_shift2 = 9'h000;
    logic [3:0] rx_bit_count2 = 4'd0;
    logic rx_alignment_locked2 = 1'b0;
    logic rx_drop_first2 = 1'b0;
    logic [3:0] rx_state2 = RX_MAGIC0;
    logic [7:0] rx_payload_index2 = 8'd0;
    logic [15:0] rx_crc2 = 16'hffff;
    logic [7:0] rx_crc_low2 = 8'h00;
    logic [7:0] job_tag_build2 = 8'h00;
    logic [639:0] job_asic_input_build2 = '0;
    logic [255:0] job_target_build2 = '0;
    logic [903:0] job_mailbox2 = '0;
    logic job_toggle2 = 1'b0;

    // The correct B byte is one of these two adjacent windows.
    // Frame magic 0x46 safely determines which window is aligned.
    wire [7:0] rx_candidate_low2 = rx_shift2[7:0];
    wire [7:0] rx_candidate_high2 = rx_shift2[8:1];

    task automatic consume_job_byte(input logic [7:0] value);
        begin
            case (rx_state2)
                RX_MAGIC0: begin
                    if (value == 8'h46)
                        rx_state2 <= RX_MAGIC1;
                end

                RX_MAGIC1: begin
                    if (value == 8'h4a)
                        rx_state2 <= RX_VERSION;
                    else if (value != 8'h46)
                        rx_state2 <= RX_MAGIC0;
                end

                RX_VERSION: begin
                    if (value == 8'h01)
                        rx_state2 <= RX_TYPE;
                    else
                        rx_state2 <= RX_MAGIC0;
                end

                RX_TYPE: begin
                    if (value == 8'h01)
                        rx_state2 <= RX_LEN0;
                    else
                        rx_state2 <= RX_MAGIC0;
                end

                RX_LEN0: begin
                    if (value == JOB_PAYLOAD_BYTES[7:0])
                        rx_state2 <= RX_LEN1;
                    else
                        rx_state2 <= RX_MAGIC0;
                end

                RX_LEN1: begin
                    if (value == 8'h00) begin
                        rx_state2 <= RX_PAYLOAD;
                        rx_payload_index2 <= 8'd0;
                        rx_crc2 <= 16'hffff;
                        job_tag_build2 <= '0;
                        job_asic_input_build2 <= '0;
                        job_target_build2 <= '0;
                    end else begin
                        rx_state2 <= RX_MAGIC0;
                    end
                end

                RX_PAYLOAD: begin
                    rx_crc2 <= crc16_byte(rx_crc2, value);

                    if (rx_payload_index2 == 7'd0)
                        job_tag_build2 <= value;
                    else if (rx_payload_index2 <= 8'd80)
                        job_asic_input_build2[
                            (rx_payload_index2 - 1'b1)*8 +: 8
                        ] <= value;
                    else
                        job_target_build2[
                            (rx_payload_index2 - 8'd81)*8 +: 8
                        ] <= value;

                    if (rx_payload_index2 == JOB_PAYLOAD_BYTES - 1)
                        rx_state2 <= RX_CRC0;
                    else
                        rx_payload_index2 <= rx_payload_index2 + 1'b1;
                end

                RX_CRC0: begin
                    rx_crc_low2 <= value;
                    rx_state2 <= RX_CRC1;
                end

                RX_CRC1: begin
                    if ({value, rx_crc_low2} == rx_crc2) begin
                        job_mailbox2 <= {
                            job_tag_build2,
                            job_target_build2,
                            job_asic_input_build2
                        };
                        job_toggle2 <= ~job_toggle2;
                    end
                    rx_state2 <= RX_MAGIC0;
                end

                default: rx_state2 <= RX_MAGIC0;
            endcase
        end
    endtask

    // Use raw JTAG TCK so CAPTURE, SHIFT and UPDATE are handled in one
    // clock domain.  The byte is consumed only after the complete DR
    // transaction length is known.
    always_ff @(posedge tck2) begin
        if (capture2) begin
            rx_shift2 <= 9'h000;
            rx_bit_count2 <= 4'd0;
        end else if (shift2) begin
            if (rx_bit_count2 < 4'd9)
                rx_shift2[rx_bit_count2] <= tdi2;

            if (rx_bit_count2 < 4'd15)
                rx_bit_count2 <= rx_bit_count2 + 1'b1;
        end else if (update2) begin
            if (rx_bit_count2 == 4'd8) begin
                // Single-device/first-device transfer.
                consume_job_byte(rx_candidate_low2);
            end else if (rx_bit_count2 >= 4'd9) begin
                // Multi-device transfer.  Acquire the byte window from
                // the first frame-magic byte, then retain it for all
                // following bytes.
                if (!rx_alignment_locked2) begin
                    if (rx_candidate_low2 == 8'h46) begin
                        rx_alignment_locked2 <= 1'b1;
                        rx_drop_first2 <= 1'b0;
                        consume_job_byte(rx_candidate_low2);
                    end else if (rx_candidate_high2 == 8'h46) begin
                        rx_alignment_locked2 <= 1'b1;
                        rx_drop_first2 <= 1'b1;
                        consume_job_byte(rx_candidate_high2);
                    end
                end else if (rx_drop_first2) begin
                    consume_job_byte(rx_candidate_high2);
                end else begin
                    consume_job_byte(rx_candidate_low2);
                end
            end
        end
    end

    wire [903:0] job_mailbox_clk;
    wire job_toggle_clk;
    logic job_seen_clk = 1'b0;

    xpm_cdc_array_single #(
        .DEST_SYNC_FF(2), .INIT_SYNC_FF(1),
        .SIM_ASSERT_CHK(0), .SRC_INPUT_REG(0), .WIDTH(904)
    ) u_job_mailbox_cdc (
        .src_clk(drck2), .src_in(job_mailbox2),
        .dest_clk(clk), .dest_out(job_mailbox_clk)
    );

    xpm_cdc_single #(
        .DEST_SYNC_FF(3), .INIT_SYNC_FF(1),
        .SIM_ASSERT_CHK(0), .SRC_INPUT_REG(0)
    ) u_job_toggle_cdc (
        .src_clk(drck2), .src_in(job_toggle2),
        .dest_clk(clk), .dest_out(job_toggle_clk)
    );

    always_ff @(posedge clk) begin
        job_pulse <= 1'b0;
        if (job_toggle_clk != job_seen_clk) begin
            job_asic_input <= job_mailbox_clk[639:0];
            job_target <= job_mailbox_clk[895:640];
            job_tag <= job_mailbox_clk[903:896];
            job_seen_clk <= job_toggle_clk;
            job_pulse <= 1'b1;
        end
    end

    // ============================================================
    // Share-frame mailbox (clk domain -> USER1 DRCK domain).
    // ============================================================
    logic [359:0] share_frame_clk = '0;
    logic share_produced_clk = 1'b0;
    logic share_consumed1 = 1'b0;
    wire share_consumed_clk;

    logic [28:0] share_pending_cycles_clk = 29'd0;
    logic share_rearm_toggle_clk = 1'b0;
    logic share_watchdog_fired_clk = 1'b0;
    wire share_rearm_toggle1;
    wire share_watchdog_fired3;

    xpm_cdc_single #(
        .DEST_SYNC_FF(3), .INIT_SYNC_FF(1),
        .SIM_ASSERT_CHK(0), .SRC_INPUT_REG(0)
    ) u_share_consumed_cdc (
        .src_clk(drck1), .src_in(share_consumed1),
        .dest_clk(clk), .dest_out(share_consumed_clk)
    );

    xpm_cdc_single #(
        .DEST_SYNC_FF(3), .INIT_SYNC_FF(1),
        .SIM_ASSERT_CHK(0), .SRC_INPUT_REG(0)
    ) u_share_rearm_cdc (
        .src_clk(clk), .src_in(share_rearm_toggle_clk),
        .dest_clk(drck1), .dest_out(share_rearm_toggle1)
    );

    xpm_cdc_single #(
        .DEST_SYNC_FF(3), .INIT_SYNC_FF(1),
        .SIM_ASSERT_CHK(0), .SRC_INPUT_REG(0)
    ) u_share_watchdog_status_cdc (
        .src_clk(clk), .src_in(share_watchdog_fired_clk),
        .dest_clk(drck3), .dest_out(share_watchdog_fired3)
    );

    assign share_ready = (share_produced_clk == share_consumed_clk);

    always_ff @(posedge clk) begin
        if (!share_ready) begin
            if (
                share_pending_cycles_clk >=
                SHARE_PENDING_TIMEOUT_CYCLES - 1
            ) begin
                // Release a response that the host failed to consume.
                // USER1 observes the rearm toggle and resets its byte
                // cursor before accepting another complete frame.
                share_pending_cycles_clk <= 29'd0;
                share_produced_clk <= share_consumed_clk;
                share_rearm_toggle_clk <= ~share_rearm_toggle_clk;
                share_watchdog_fired_clk <= 1'b1;
            end else begin
                share_pending_cycles_clk <=
                    share_pending_cycles_clk + 1'b1;
            end
        end else begin
            share_pending_cycles_clk <= 29'd0;

            if (share_valid) begin
                share_frame_clk <= make_share_frame(
                    share_tag,
                    share_nonce,
                    share_digest
                );
                share_produced_clk <= ~share_produced_clk;
            end
        end
    end

    logic [8:0] user1_shift = 9'h100;
    logic [3:0] user1_bit_count = 4'd0;
    logic [5:0] share_byte_index1 = 6'd0;
    logic user1_had_data = 1'b0;
    logic captured_share_toggle1 = 1'b0;
    logic share_rearm_seen1 = 1'b0;

    always_ff @(posedge drck1) begin
        if (
            reset1 ||
            (share_rearm_toggle1 != share_rearm_seen1)
        ) begin
            user1_shift <= 9'h100;
            user1_bit_count <= 4'd0;
            share_byte_index1 <= 6'd0;
            user1_had_data <= 1'b0;
            captured_share_toggle1 <= share_produced_clk;
            share_consumed1 <= share_produced_clk;
            share_rearm_seen1 <= share_rearm_toggle1;
        end else if (capture1) begin
            user1_bit_count <= 4'd0;
            captured_share_toggle1 <= share_produced_clk;

            if (share_produced_clk != share_consumed1) begin
                user1_shift <= {
                    1'b0,
                    share_frame_clk[share_byte_index1*8 +: 8]
                };
                user1_had_data <= 1'b1;
            end else begin
                user1_shift <= 9'h100;
                user1_had_data <= 1'b0;
            end
        end else if (shift1) begin
            user1_shift <= {1'b0, user1_shift[8:1]};

            if ((user1_bit_count == 4'd7) && user1_had_data) begin
                user1_had_data <= 1'b0;
                if (share_byte_index1 == SHARE_FRAME_BYTES - 1) begin
                    share_byte_index1 <= 6'd0;
                    share_consumed1 <= captured_share_toggle1;
                end else begin
                    share_byte_index1 <= share_byte_index1 + 1'b1;
                end
            end
            user1_bit_count <= user1_bit_count + 1'b1;
        end
    end

    // USER3 legacy status bit 2: at least one outbound byte is ready.
    logic [31:0] user3_shift = 32'h00000000;
    always_ff @(posedge drck3) begin
        if (capture3) begin
            // Bit 2 retains the legacy data-ready indication.
            // Bit 3 is sticky after an outbound-mailbox recovery.
            user3_shift <= {
                28'h0000000,
                share_watchdog_fired3,
                (share_produced_clk != share_consumed1),
                2'b00
            };
        end else if (shift3) begin
            user3_shift <= {1'b0, user3_shift[31:1]};
        end
    end

    BSCANE2 #(.JTAG_CHAIN(1)) u_user1 (
        .CAPTURE(capture1), .DRCK(drck1),
        .RESET(reset1), .RUNTEST(runtest1),
        .SEL(sel1), .SHIFT(shift1),
        .TCK(tck1), .TDI(tdi1),
        .TMS(tms1), .UPDATE(update1),
        .TDO(user1_shift[0])
    );

    BSCANE2 #(.JTAG_CHAIN(2)) u_user2 (
        .CAPTURE(capture2), .DRCK(drck2),
        .RESET(reset2), .RUNTEST(runtest2),
        .SEL(sel2), .SHIFT(shift2),
        .TCK(tck2), .TDI(tdi2),
        .TMS(tms2), .UPDATE(update2),
        .TDO(1'b0)
    );

    BSCANE2 #(.JTAG_CHAIN(3)) u_user3 (
        .CAPTURE(capture3), .DRCK(drck3),
        .RESET(reset3), .RUNTEST(runtest3),
        .SEL(sel3), .SHIFT(shift3),
        .TCK(tck3), .TDI(tdi3),
        .TMS(tms3), .UPDATE(update3),
        .TDO(user3_shift[0])
    );

endmodule
