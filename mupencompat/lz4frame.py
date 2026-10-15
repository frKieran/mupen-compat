"""LZ4 frame decoding in plain Python (Mupen 1.5.0+ writes savestates as LZ4 frames), so the app needs no
extra library."""
import struct


def _lz4_block(src, i, end, out):
    """Decode one LZ4 block (src[i:end]) onto the end of out. Matches may reach back into earlier blocks,
    which is what linked blocks in a frame rely on."""
    while i < end:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= end:                     # the last sequence is literals only
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        mlen = token & 15
        if mlen == 15:
            while True:
                b = src[i]
                i += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(out) - offset
        if offset >= mlen:
            out += out[start:start + mlen]
        else:                            # overlapping copy: the last `offset` bytes repeat
            pattern = bytes(out[start:])
            out += (pattern * (mlen // offset + 1))[:mlen]


def lz4_frame_decompress(data):
    """The LZ4 frame format (as Mupen64 1.5.0 writes savestates), in plain Python so that neither the
    solver nor the packaged app needs an extra library."""
    try:
        import lz4.frame
        return lz4.frame.decompress(data)
    except ImportError:
        pass
    out, i = bytearray(), 0
    while i + 4 <= len(data):
        magic = struct.unpack_from('<I', data, i)[0]
        if 0x184D2A50 <= magic <= 0x184D2A5F:            # skippable frame
            i += 8 + struct.unpack_from('<I', data, i + 4)[0]
            continue
        if magic != 0x184D2204:
            raise ValueError('not an LZ4 frame')
        flg = data[i + 4]
        i += 6 + (8 if flg & 0x08 else 0) + (4 if flg & 0x01 else 0) + 1   # descriptor, sizes, header checksum
        block_sum = 4 if flg & 0x10 else 0
        while True:
            size = struct.unpack_from('<I', data, i)[0]
            i += 4
            if size == 0:                                # end mark
                break
            n = size & 0x7FFFFFFF
            if size & 0x80000000:                        # stored uncompressed
                out += data[i:i + n]
            else:
                _lz4_block(data, i, i + n, out)
            i += n + block_sum
        if flg & 0x04:                                   # content checksum
            i += 4
    return bytes(out)
