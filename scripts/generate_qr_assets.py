import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def gf_tables():
    exp = [0] * 512
    log = [0] * 256
    x = 1
    for i in range(255):
        exp[i] = x
        log[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        exp[i] = exp[i - 255]
    return exp, log


GF_EXP, GF_LOG = gf_tables()


def gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return GF_EXP[GF_LOG[a] + GF_LOG[b]]


def poly_mul(a, b):
    out = [0] * (len(a) + len(b) - 1)
    for i, av in enumerate(a):
        for j, bv in enumerate(b):
            out[i + j] ^= gf_mul(av, bv)
    return out


def rs_generator(degree):
    poly = [1]
    for i in range(degree):
        poly = poly_mul(poly, [1, GF_EXP[i]])
    return poly


def rs_remainder(data, degree):
    gen = rs_generator(degree)
    rem = list(data) + [0] * degree
    for i in range(len(data)):
        factor = rem[i]
        if factor:
            for j, coeff in enumerate(gen):
                rem[i + j] ^= gf_mul(coeff, factor)
    return rem[-degree:]


class BitBuffer:
    def __init__(self):
        self.bits = []

    def append(self, value, width):
        for i in range(width - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def to_codewords(self):
        out = []
        for i in range(0, len(self.bits), 8):
            byte = 0
            for bit in self.bits[i : i + 8]:
                byte = (byte << 1) | bit
            out.append(byte)
        return out


def numeric_payload(text):
    if not text.isdigit():
        raise ValueError(f"QR ticketId must be numeric for this generator: {text!r}")
    if len(text) > 41:
        raise ValueError("Version 1-L QR only supports up to 41 numeric digits.")

    bits = BitBuffer()
    bits.append(0b0001, 4)
    bits.append(len(text), 10)
    for i in range(0, len(text), 3):
        group = text[i : i + 3]
        bits.append(int(group), {1: 4, 2: 7, 3: 10}[len(group)])

    capacity_bits = 19 * 8
    bits.append(0, min(4, capacity_bits - len(bits.bits)))
    while len(bits.bits) % 8:
        bits.append(0, 1)

    data = bits.to_codewords()
    pad = [0xEC, 0x11]
    i = 0
    while len(data) < 19:
        data.append(pad[i % 2])
        i += 1
    return data


def reserve_square(reserved, x0, y0, w, h):
    for y in range(y0, y0 + h):
        for x in range(x0, x0 + w):
            if 0 <= x < 21 and 0 <= y < 21:
                reserved[y][x] = True


def make_base():
    n = 21
    modules = [[False for _ in range(n)] for _ in range(n)]
    reserved = [[False for _ in range(n)] for _ in range(n)]

    def finder(x0, y0):
        for y in range(-1, 8):
            for x in range(-1, 8):
                xx, yy = x0 + x, y0 + y
                if 0 <= xx < n and 0 <= yy < n:
                    reserved[yy][xx] = True
                    if 0 <= x <= 6 and 0 <= y <= 6:
                        modules[yy][xx] = (
                            x in (0, 6)
                            or y in (0, 6)
                            or (2 <= x <= 4 and 2 <= y <= 4)
                        )

    finder(0, 0)
    finder(n - 7, 0)
    finder(0, n - 7)

    for i in range(8, n - 8):
        modules[6][i] = i % 2 == 0
        modules[i][6] = i % 2 == 0
        reserved[6][i] = True
        reserved[i][6] = True

    modules[13][8] = True
    reserved[13][8] = True

    reserve_square(reserved, 0, 8, 9, 1)
    reserve_square(reserved, 8, 0, 1, 9)
    reserve_square(reserved, n - 8, 8, 8, 1)
    reserve_square(reserved, 8, n - 7, 1, 7)
    return modules, reserved


def mask_bit(mask, x, y):
    return [
        (x + y) % 2 == 0,
        y % 2 == 0,
        x % 3 == 0,
        (x + y) % 3 == 0,
        (y // 2 + x // 3) % 2 == 0,
        ((x * y) % 2 + (x * y) % 3) == 0,
        (((x * y) % 2 + (x * y) % 3) % 2) == 0,
        (((x + y) % 2 + (x * y) % 3) % 2) == 0,
    ][mask]


def add_data(modules, reserved, bits, mask):
    n = 21
    i = 0
    upward = True
    x = n - 1
    while x > 0:
        if x == 6:
            x -= 1
        ys = range(n - 1, -1, -1) if upward else range(n)
        for y in ys:
            for xx in (x, x - 1):
                if not reserved[y][xx]:
                    bit = bits[i] if i < len(bits) else 0
                    modules[y][xx] = bool(bit) ^ mask_bit(mask, xx, y)
                    i += 1
        upward = not upward
        x -= 2


def format_bits(mask):
    value = (0b01 << 3) | mask
    data = value << 10
    gen = 0x537
    for i in range(14, 9, -1):
        if (data >> i) & 1:
            data ^= gen << (i - 10)
    return ((value << 10) | data) ^ 0x5412


def add_format(modules, mask):
    bits = format_bits(mask)
    coords1 = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
               (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    coords2 = [(20, 8), (19, 8), (18, 8), (17, 8), (16, 8), (15, 8), (14, 8), (13, 8),
               (8, 14), (8, 15), (8, 16), (8, 17), (8, 18), (8, 19), (8, 20)]
    for i, (x, y) in enumerate(coords1):
        modules[y][x] = bool((bits >> i) & 1)
    for i, (x, y) in enumerate(coords2):
        modules[y][x] = bool((bits >> i) & 1)


def penalty(modules):
    n = len(modules)
    score = 0
    for rows in (modules, [[modules[y][x] for y in range(n)] for x in range(n)]):
        for row in rows:
            run_color = row[0]
            run = 1
            for cell in row[1:]:
                if cell == run_color:
                    run += 1
                else:
                    if run >= 5:
                        score += 3 + (run - 5)
                    run_color = cell
                    run = 1
            if run >= 5:
                score += 3 + (run - 5)
    for y in range(n - 1):
        for x in range(n - 1):
            if modules[y][x] == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
                score += 3
    pattern = [True, False, True, True, True, False, True, False, False, False, False]
    reverse_pattern = list(reversed(pattern))
    for y in range(n):
        for x in range(n - 10):
            row = [modules[y][x + i] for i in range(11)]
            if row == pattern or row == reverse_pattern:
                score += 40
    for x in range(n):
        for y in range(n - 10):
            col = [modules[y + i][x] for i in range(11)]
            if col == pattern or col == reverse_pattern:
                score += 40
    dark = sum(1 for row in modules for cell in row if cell)
    score += abs(dark * 20 // (n * n) - 10) * 10
    return score


def qr_matrix(text):
    data = numeric_payload(text)
    codewords = data + rs_remainder(data, 7)
    bits = []
    for byte in codewords:
        bits.extend((byte >> i) & 1 for i in range(7, -1, -1))

    best = None
    for mask in range(8):
        modules, reserved = make_base()
        add_data(modules, reserved, bits, mask)
        add_format(modules, mask)
        score = penalty(modules)
        if best is None or score < best[0]:
            best = (score, modules)
    return best[1]


def save_qr(text, path, pixels=500):
    modules = qr_matrix(text)
    size = len(modules)
    scale = 23
    margin = 8
    img = Image.new("RGB", (pixels, pixels), "white")
    px = img.load()
    for y, row in enumerate(modules):
        for x, dark in enumerate(row):
            if dark:
                x0 = margin + x * scale
                y0 = margin + y * scale
                for yy in range(y0, y0 + scale):
                    for xx in range(x0, x0 + scale):
                        px[xx, yy] = (0, 0, 0)
    img.save(path)


def main():
    config_path = ROOT / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    for index, ticket in enumerate(config["tickets"], start=1):
        target = ticket.get("qr") or f"assets/qr-{index}.png"
        save_qr(str(ticket["ticketId"]), ROOT / target)
        print(f"{target} <- {ticket['ticketId']}")


if __name__ == "__main__":
    main()
