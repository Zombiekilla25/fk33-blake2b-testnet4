`timescale 1ns/1ps

/*
 * RFC 7693 unkeyed BLAKE2b, single final block, fully unrolled pipeline.
 *
 * One block may enter on every clock.  Each of the 24 BLAKE2b half-rounds
 * is divided into two registered G phases, eliminating all runtime round
 * selection and limiting a stage to two dependent 64-bit additions.
 *
 * block_in byte zero is block_in[7:0].  message_bytes must be 0..128.
 * digest_bytes must be 1..64.  digest_out byte zero is digest_out[7:0].
 * Only the first digest_bytes bytes are part of the requested digest.
 */
module blake2b_unrolled48 (
    input  logic          clk,
    input  logic          rst,
    input  logic          valid_in,
    input  logic [1023:0] block_in,
    input  logic [7:0]    message_bytes,
    input  logic [6:0]    digest_bytes,
    output logic          valid_out,
    output logic [511:0]  digest_out
);

    localparam logic [63:0] IV0 = 64'h6a09e667f3bcc908;
    localparam logic [63:0] IV1 = 64'hbb67ae8584caa73b;
    localparam logic [63:0] IV2 = 64'h3c6ef372fe94f82b;
    localparam logic [63:0] IV3 = 64'ha54ff53a5f1d36f1;
    localparam logic [63:0] IV4 = 64'h510e527fade682d1;
    localparam logic [63:0] IV5 = 64'h9b05688c2b3e6c1f;
    localparam logic [63:0] IV6 = 64'h1f83d9abfb41bd6b;
    localparam logic [63:0] IV7 = 64'h5be0cd19137e2179;

    function automatic integer sigma_index(
        input integer round_number,
        input integer position
    );
        begin
            case (round_number)
                0: case (position)
                    0:sigma_index=0;  1:sigma_index=1;
                    2:sigma_index=2;  3:sigma_index=3;
                    4:sigma_index=4;  5:sigma_index=5;
                    6:sigma_index=6;  7:sigma_index=7;
                    8:sigma_index=8;  9:sigma_index=9;
                    10:sigma_index=10; 11:sigma_index=11;
                    12:sigma_index=12; 13:sigma_index=13;
                    14:sigma_index=14; default:sigma_index=15;
                endcase
                1: case (position)
                    0:sigma_index=14; 1:sigma_index=10;
                    2:sigma_index=4;  3:sigma_index=8;
                    4:sigma_index=9;  5:sigma_index=15;
                    6:sigma_index=13; 7:sigma_index=6;
                    8:sigma_index=1;  9:sigma_index=12;
                    10:sigma_index=0; 11:sigma_index=2;
                    12:sigma_index=11; 13:sigma_index=7;
                    14:sigma_index=5; default:sigma_index=3;
                endcase
                2: case (position)
                    0:sigma_index=11; 1:sigma_index=8;
                    2:sigma_index=12; 3:sigma_index=0;
                    4:sigma_index=5;  5:sigma_index=2;
                    6:sigma_index=15; 7:sigma_index=13;
                    8:sigma_index=10; 9:sigma_index=14;
                    10:sigma_index=3; 11:sigma_index=6;
                    12:sigma_index=7; 13:sigma_index=1;
                    14:sigma_index=9; default:sigma_index=4;
                endcase
                3: case (position)
                    0:sigma_index=7;  1:sigma_index=9;
                    2:sigma_index=3;  3:sigma_index=1;
                    4:sigma_index=13; 5:sigma_index=12;
                    6:sigma_index=11; 7:sigma_index=14;
                    8:sigma_index=2;  9:sigma_index=6;
                    10:sigma_index=5; 11:sigma_index=10;
                    12:sigma_index=4; 13:sigma_index=0;
                    14:sigma_index=15; default:sigma_index=8;
                endcase
                4: case (position)
                    0:sigma_index=9;  1:sigma_index=0;
                    2:sigma_index=5;  3:sigma_index=7;
                    4:sigma_index=2;  5:sigma_index=4;
                    6:sigma_index=10; 7:sigma_index=15;
                    8:sigma_index=14; 9:sigma_index=1;
                    10:sigma_index=11; 11:sigma_index=12;
                    12:sigma_index=6; 13:sigma_index=8;
                    14:sigma_index=3; default:sigma_index=13;
                endcase
                5: case (position)
                    0:sigma_index=2;  1:sigma_index=12;
                    2:sigma_index=6;  3:sigma_index=10;
                    4:sigma_index=0;  5:sigma_index=11;
                    6:sigma_index=8;  7:sigma_index=3;
                    8:sigma_index=4;  9:sigma_index=13;
                    10:sigma_index=7; 11:sigma_index=5;
                    12:sigma_index=15; 13:sigma_index=14;
                    14:sigma_index=1; default:sigma_index=9;
                endcase
                6: case (position)
                    0:sigma_index=12; 1:sigma_index=5;
                    2:sigma_index=1;  3:sigma_index=15;
                    4:sigma_index=14; 5:sigma_index=13;
                    6:sigma_index=4;  7:sigma_index=10;
                    8:sigma_index=0;  9:sigma_index=7;
                    10:sigma_index=6; 11:sigma_index=3;
                    12:sigma_index=9; 13:sigma_index=2;
                    14:sigma_index=8; default:sigma_index=11;
                endcase
                7: case (position)
                    0:sigma_index=13; 1:sigma_index=11;
                    2:sigma_index=7;  3:sigma_index=14;
                    4:sigma_index=12; 5:sigma_index=1;
                    6:sigma_index=3;  7:sigma_index=9;
                    8:sigma_index=5;  9:sigma_index=0;
                    10:sigma_index=15; 11:sigma_index=4;
                    12:sigma_index=8; 13:sigma_index=6;
                    14:sigma_index=2; default:sigma_index=10;
                endcase
                8: case (position)
                    0:sigma_index=6;  1:sigma_index=15;
                    2:sigma_index=14; 3:sigma_index=9;
                    4:sigma_index=11; 5:sigma_index=3;
                    6:sigma_index=0;  7:sigma_index=8;
                    8:sigma_index=12; 9:sigma_index=2;
                    10:sigma_index=13; 11:sigma_index=7;
                    12:sigma_index=1; 13:sigma_index=4;
                    14:sigma_index=10; default:sigma_index=5;
                endcase
                9: case (position)
                    0:sigma_index=10; 1:sigma_index=2;
                    2:sigma_index=8;  3:sigma_index=4;
                    4:sigma_index=7;  5:sigma_index=6;
                    6:sigma_index=1;  7:sigma_index=5;
                    8:sigma_index=15; 9:sigma_index=11;
                    10:sigma_index=9; 11:sigma_index=14;
                    12:sigma_index=3; 13:sigma_index=12;
                    14:sigma_index=13; default:sigma_index=0;
                endcase
                10: case (position)
                    0:sigma_index=0;  1:sigma_index=1;
                    2:sigma_index=2;  3:sigma_index=3;
                    4:sigma_index=4;  5:sigma_index=5;
                    6:sigma_index=6;  7:sigma_index=7;
                    8:sigma_index=8;  9:sigma_index=9;
                    10:sigma_index=10; 11:sigma_index=11;
                    12:sigma_index=12; 13:sigma_index=13;
                    14:sigma_index=14; default:sigma_index=15;
                endcase
                default: case (position)
                    0:sigma_index=14; 1:sigma_index=10;
                    2:sigma_index=4;  3:sigma_index=8;
                    4:sigma_index=9;  5:sigma_index=15;
                    6:sigma_index=13; 7:sigma_index=6;
                    8:sigma_index=1;  9:sigma_index=12;
                    10:sigma_index=0; 11:sigma_index=2;
                    12:sigma_index=11; 13:sigma_index=7;
                    14:sigma_index=5; default:sigma_index=3;
                endcase
            endcase
        end
    endfunction

    function automatic logic [63:0] rotr64(
        input logic [63:0] value,
        input integer amount
    );
        rotr64 = (value >> amount) | (value << (64 - amount));
    endfunction

    function automatic logic [255:0] g_first(
        input logic [63:0] a,
        input logic [63:0] b,
        input logic [63:0] c,
        input logic [63:0] d,
        input logic [63:0] x
    );
        logic [63:0] aa;
        logic [63:0] bb;
        logic [63:0] cc;
        logic [63:0] dd;
        begin
            aa = a + b + x;
            dd = rotr64(d ^ aa, 32);
            cc = c + dd;
            bb = rotr64(b ^ cc, 24);
            g_first = {aa, bb, cc, dd};
        end
    endfunction

    function automatic logic [255:0] g_second(
        input logic [63:0] a,
        input logic [63:0] b,
        input logic [63:0] c,
        input logic [63:0] d,
        input logic [63:0] y
    );
        logic [63:0] aa;
        logic [63:0] bb;
        logic [63:0] cc;
        logic [63:0] dd;
        begin
            aa = a + b + y;
            dd = rotr64(d ^ aa, 16);
            cc = c + dd;
            bb = rotr64(b ^ cc, 63);
            g_second = {aa, bb, cc, dd};
        end
    endfunction

    logic [63:0]   v_pipe [0:48][0:15];
    logic [1023:0] m_pipe [0:48];
    logic [6:0]    dlen_pipe [0:48];
    logic          valid_pipe [0:48];

    always_ff @(posedge clk) begin
        if (rst) begin
            valid_pipe[0] <= 1'b0;
        end else begin
            valid_pipe[0] <= valid_in;
            if (valid_in) begin
                m_pipe[0] <= block_in;
                dlen_pipe[0] <= digest_bytes;

                v_pipe[0][0] <= IV0 ^
                    (64'h0000000001010000 | {57'd0, digest_bytes});
                v_pipe[0][1] <= IV1;
                v_pipe[0][2] <= IV2;
                v_pipe[0][3] <= IV3;
                v_pipe[0][4] <= IV4;
                v_pipe[0][5] <= IV5;
                v_pipe[0][6] <= IV6;
                v_pipe[0][7] <= IV7;
                v_pipe[0][8] <= IV0;
                v_pipe[0][9] <= IV1;
                v_pipe[0][10] <= IV2;
                v_pipe[0][11] <= IV3;
                v_pipe[0][12] <= IV4 ^ {56'd0, message_bytes};
                v_pipe[0][13] <= IV5;
                v_pipe[0][14] <= IV6 ^ 64'hffffffffffffffff;
                v_pipe[0][15] <= IV7;
            end
        end
    end

    generate
        for (genvar half_round = 0;
             half_round < 24;
             half_round = half_round + 1) begin : g_half_round

            localparam integer ROUND_NUMBER = half_round / 2;
            localparam integer DIAGONAL = half_round % 2;
            localparam integer INPUT_STAGE = half_round * 2;
            localparam integer MIDDLE_STAGE = INPUT_STAGE + 1;
            localparam integer OUTPUT_STAGE = INPUT_STAGE + 2;

            localparam integer S0 = sigma_index(ROUND_NUMBER, 0);
            localparam integer S1 = sigma_index(ROUND_NUMBER, 1);
            localparam integer S2 = sigma_index(ROUND_NUMBER, 2);
            localparam integer S3 = sigma_index(ROUND_NUMBER, 3);
            localparam integer S4 = sigma_index(ROUND_NUMBER, 4);
            localparam integer S5 = sigma_index(ROUND_NUMBER, 5);
            localparam integer S6 = sigma_index(ROUND_NUMBER, 6);
            localparam integer S7 = sigma_index(ROUND_NUMBER, 7);
            localparam integer S8 = sigma_index(ROUND_NUMBER, 8);
            localparam integer S9 = sigma_index(ROUND_NUMBER, 9);
            localparam integer S10 = sigma_index(ROUND_NUMBER, 10);
            localparam integer S11 = sigma_index(ROUND_NUMBER, 11);
            localparam integer S12 = sigma_index(ROUND_NUMBER, 12);
            localparam integer S13 = sigma_index(ROUND_NUMBER, 13);
            localparam integer S14 = sigma_index(ROUND_NUMBER, 14);
            localparam integer S15 = sigma_index(ROUND_NUMBER, 15);

            if (DIAGONAL == 0) begin : g_column
                wire [255:0] first0 = g_first(
                    v_pipe[INPUT_STAGE][0], v_pipe[INPUT_STAGE][4],
                    v_pipe[INPUT_STAGE][8], v_pipe[INPUT_STAGE][12],
                    m_pipe[INPUT_STAGE][64*S0 +: 64]);
                wire [255:0] first1 = g_first(
                    v_pipe[INPUT_STAGE][1], v_pipe[INPUT_STAGE][5],
                    v_pipe[INPUT_STAGE][9], v_pipe[INPUT_STAGE][13],
                    m_pipe[INPUT_STAGE][64*S2 +: 64]);
                wire [255:0] first2 = g_first(
                    v_pipe[INPUT_STAGE][2], v_pipe[INPUT_STAGE][6],
                    v_pipe[INPUT_STAGE][10], v_pipe[INPUT_STAGE][14],
                    m_pipe[INPUT_STAGE][64*S4 +: 64]);
                wire [255:0] first3 = g_first(
                    v_pipe[INPUT_STAGE][3], v_pipe[INPUT_STAGE][7],
                    v_pipe[INPUT_STAGE][11], v_pipe[INPUT_STAGE][15],
                    m_pipe[INPUT_STAGE][64*S6 +: 64]);

                always_ff @(posedge clk) begin
                    if (rst) begin
                        valid_pipe[MIDDLE_STAGE] <= 1'b0;
                    end else begin
                        valid_pipe[MIDDLE_STAGE] <= valid_pipe[INPUT_STAGE];
                        if (valid_pipe[INPUT_STAGE]) begin
                            m_pipe[MIDDLE_STAGE] <= m_pipe[INPUT_STAGE];
                            dlen_pipe[MIDDLE_STAGE] <=
                                dlen_pipe[INPUT_STAGE];
                            {v_pipe[MIDDLE_STAGE][0],
                             v_pipe[MIDDLE_STAGE][4],
                             v_pipe[MIDDLE_STAGE][8],
                             v_pipe[MIDDLE_STAGE][12]} <= first0;
                            {v_pipe[MIDDLE_STAGE][1],
                             v_pipe[MIDDLE_STAGE][5],
                             v_pipe[MIDDLE_STAGE][9],
                             v_pipe[MIDDLE_STAGE][13]} <= first1;
                            {v_pipe[MIDDLE_STAGE][2],
                             v_pipe[MIDDLE_STAGE][6],
                             v_pipe[MIDDLE_STAGE][10],
                             v_pipe[MIDDLE_STAGE][14]} <= first2;
                            {v_pipe[MIDDLE_STAGE][3],
                             v_pipe[MIDDLE_STAGE][7],
                             v_pipe[MIDDLE_STAGE][11],
                             v_pipe[MIDDLE_STAGE][15]} <= first3;
                        end
                    end
                end

                wire [255:0] second0 = g_second(
                    v_pipe[MIDDLE_STAGE][0], v_pipe[MIDDLE_STAGE][4],
                    v_pipe[MIDDLE_STAGE][8], v_pipe[MIDDLE_STAGE][12],
                    m_pipe[MIDDLE_STAGE][64*S1 +: 64]);
                wire [255:0] second1 = g_second(
                    v_pipe[MIDDLE_STAGE][1], v_pipe[MIDDLE_STAGE][5],
                    v_pipe[MIDDLE_STAGE][9], v_pipe[MIDDLE_STAGE][13],
                    m_pipe[MIDDLE_STAGE][64*S3 +: 64]);
                wire [255:0] second2 = g_second(
                    v_pipe[MIDDLE_STAGE][2], v_pipe[MIDDLE_STAGE][6],
                    v_pipe[MIDDLE_STAGE][10], v_pipe[MIDDLE_STAGE][14],
                    m_pipe[MIDDLE_STAGE][64*S5 +: 64]);
                wire [255:0] second3 = g_second(
                    v_pipe[MIDDLE_STAGE][3], v_pipe[MIDDLE_STAGE][7],
                    v_pipe[MIDDLE_STAGE][11], v_pipe[MIDDLE_STAGE][15],
                    m_pipe[MIDDLE_STAGE][64*S7 +: 64]);

                always_ff @(posedge clk) begin
                    if (rst) begin
                        valid_pipe[OUTPUT_STAGE] <= 1'b0;
                    end else begin
                        valid_pipe[OUTPUT_STAGE] <=
                            valid_pipe[MIDDLE_STAGE];
                        if (valid_pipe[MIDDLE_STAGE]) begin
                            m_pipe[OUTPUT_STAGE] <= m_pipe[MIDDLE_STAGE];
                            dlen_pipe[OUTPUT_STAGE] <=
                                dlen_pipe[MIDDLE_STAGE];
                            {v_pipe[OUTPUT_STAGE][0],
                             v_pipe[OUTPUT_STAGE][4],
                             v_pipe[OUTPUT_STAGE][8],
                             v_pipe[OUTPUT_STAGE][12]} <= second0;
                            {v_pipe[OUTPUT_STAGE][1],
                             v_pipe[OUTPUT_STAGE][5],
                             v_pipe[OUTPUT_STAGE][9],
                             v_pipe[OUTPUT_STAGE][13]} <= second1;
                            {v_pipe[OUTPUT_STAGE][2],
                             v_pipe[OUTPUT_STAGE][6],
                             v_pipe[OUTPUT_STAGE][10],
                             v_pipe[OUTPUT_STAGE][14]} <= second2;
                            {v_pipe[OUTPUT_STAGE][3],
                             v_pipe[OUTPUT_STAGE][7],
                             v_pipe[OUTPUT_STAGE][11],
                             v_pipe[OUTPUT_STAGE][15]} <= second3;
                        end
                    end
                end
            end else begin : g_diagonal
                wire [255:0] first0 = g_first(
                    v_pipe[INPUT_STAGE][0], v_pipe[INPUT_STAGE][5],
                    v_pipe[INPUT_STAGE][10], v_pipe[INPUT_STAGE][15],
                    m_pipe[INPUT_STAGE][64*S8 +: 64]);
                wire [255:0] first1 = g_first(
                    v_pipe[INPUT_STAGE][1], v_pipe[INPUT_STAGE][6],
                    v_pipe[INPUT_STAGE][11], v_pipe[INPUT_STAGE][12],
                    m_pipe[INPUT_STAGE][64*S10 +: 64]);
                wire [255:0] first2 = g_first(
                    v_pipe[INPUT_STAGE][2], v_pipe[INPUT_STAGE][7],
                    v_pipe[INPUT_STAGE][8], v_pipe[INPUT_STAGE][13],
                    m_pipe[INPUT_STAGE][64*S12 +: 64]);
                wire [255:0] first3 = g_first(
                    v_pipe[INPUT_STAGE][3], v_pipe[INPUT_STAGE][4],
                    v_pipe[INPUT_STAGE][9], v_pipe[INPUT_STAGE][14],
                    m_pipe[INPUT_STAGE][64*S14 +: 64]);

                always_ff @(posedge clk) begin
                    if (rst) begin
                        valid_pipe[MIDDLE_STAGE] <= 1'b0;
                    end else begin
                        valid_pipe[MIDDLE_STAGE] <= valid_pipe[INPUT_STAGE];
                        if (valid_pipe[INPUT_STAGE]) begin
                            m_pipe[MIDDLE_STAGE] <= m_pipe[INPUT_STAGE];
                            dlen_pipe[MIDDLE_STAGE] <=
                                dlen_pipe[INPUT_STAGE];
                            {v_pipe[MIDDLE_STAGE][0],
                             v_pipe[MIDDLE_STAGE][5],
                             v_pipe[MIDDLE_STAGE][10],
                             v_pipe[MIDDLE_STAGE][15]} <= first0;
                            {v_pipe[MIDDLE_STAGE][1],
                             v_pipe[MIDDLE_STAGE][6],
                             v_pipe[MIDDLE_STAGE][11],
                             v_pipe[MIDDLE_STAGE][12]} <= first1;
                            {v_pipe[MIDDLE_STAGE][2],
                             v_pipe[MIDDLE_STAGE][7],
                             v_pipe[MIDDLE_STAGE][8],
                             v_pipe[MIDDLE_STAGE][13]} <= first2;
                            {v_pipe[MIDDLE_STAGE][3],
                             v_pipe[MIDDLE_STAGE][4],
                             v_pipe[MIDDLE_STAGE][9],
                             v_pipe[MIDDLE_STAGE][14]} <= first3;
                        end
                    end
                end

                wire [255:0] second0 = g_second(
                    v_pipe[MIDDLE_STAGE][0], v_pipe[MIDDLE_STAGE][5],
                    v_pipe[MIDDLE_STAGE][10], v_pipe[MIDDLE_STAGE][15],
                    m_pipe[MIDDLE_STAGE][64*S9 +: 64]);
                wire [255:0] second1 = g_second(
                    v_pipe[MIDDLE_STAGE][1], v_pipe[MIDDLE_STAGE][6],
                    v_pipe[MIDDLE_STAGE][11], v_pipe[MIDDLE_STAGE][12],
                    m_pipe[MIDDLE_STAGE][64*S11 +: 64]);
                wire [255:0] second2 = g_second(
                    v_pipe[MIDDLE_STAGE][2], v_pipe[MIDDLE_STAGE][7],
                    v_pipe[MIDDLE_STAGE][8], v_pipe[MIDDLE_STAGE][13],
                    m_pipe[MIDDLE_STAGE][64*S13 +: 64]);
                wire [255:0] second3 = g_second(
                    v_pipe[MIDDLE_STAGE][3], v_pipe[MIDDLE_STAGE][4],
                    v_pipe[MIDDLE_STAGE][9], v_pipe[MIDDLE_STAGE][14],
                    m_pipe[MIDDLE_STAGE][64*S15 +: 64]);

                always_ff @(posedge clk) begin
                    if (rst) begin
                        valid_pipe[OUTPUT_STAGE] <= 1'b0;
                    end else begin
                        valid_pipe[OUTPUT_STAGE] <=
                            valid_pipe[MIDDLE_STAGE];
                        if (valid_pipe[MIDDLE_STAGE]) begin
                            m_pipe[OUTPUT_STAGE] <= m_pipe[MIDDLE_STAGE];
                            dlen_pipe[OUTPUT_STAGE] <=
                                dlen_pipe[MIDDLE_STAGE];
                            {v_pipe[OUTPUT_STAGE][0],
                             v_pipe[OUTPUT_STAGE][5],
                             v_pipe[OUTPUT_STAGE][10],
                             v_pipe[OUTPUT_STAGE][15]} <= second0;
                            {v_pipe[OUTPUT_STAGE][1],
                             v_pipe[OUTPUT_STAGE][6],
                             v_pipe[OUTPUT_STAGE][11],
                             v_pipe[OUTPUT_STAGE][12]} <= second1;
                            {v_pipe[OUTPUT_STAGE][2],
                             v_pipe[OUTPUT_STAGE][7],
                             v_pipe[OUTPUT_STAGE][8],
                             v_pipe[OUTPUT_STAGE][13]} <= second2;
                            {v_pipe[OUTPUT_STAGE][3],
                             v_pipe[OUTPUT_STAGE][4],
                             v_pipe[OUTPUT_STAGE][9],
                             v_pipe[OUTPUT_STAGE][14]} <= second3;
                        end
                    end
                end
            end
        end
    endgenerate

    always_comb begin
        valid_out = valid_pipe[48];
        digest_out = '0;
        digest_out[0 +: 64] =
            (IV0 ^ (64'h0000000001010000 |
                    {57'd0, dlen_pipe[48]})) ^
            v_pipe[48][0] ^ v_pipe[48][8];
        digest_out[64 +: 64] = IV1 ^
            v_pipe[48][1] ^ v_pipe[48][9];
        digest_out[128 +: 64] = IV2 ^
            v_pipe[48][2] ^ v_pipe[48][10];
        digest_out[192 +: 64] = IV3 ^
            v_pipe[48][3] ^ v_pipe[48][11];
        digest_out[256 +: 64] = IV4 ^
            v_pipe[48][4] ^ v_pipe[48][12];
        digest_out[320 +: 64] = IV5 ^
            v_pipe[48][5] ^ v_pipe[48][13];
        digest_out[384 +: 64] = IV6 ^
            v_pipe[48][6] ^ v_pipe[48][14];
        digest_out[448 +: 64] = IV7 ^
            v_pipe[48][7] ^ v_pipe[48][15];
    end

endmodule
