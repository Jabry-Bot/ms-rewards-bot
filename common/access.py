"""
Activación de cada equipo con un código de 6 caracteres.

Lo comparten el instalador, el panel y el bot. El código en claro no está en
ninguna parte del repo: solo su huella (scrypt con sal propia), que no permite
deducirlo. Una vez aceptado, el equipo queda activado con un archivo cifrado con
DPAPI (solo lo puede leer este mismo usuario de Windows en este mismo equipo).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
from datetime import datetime
from pathlib import Path

CODE_LEN = 6
ACCESS_FILE = "access.bin"
_ENTROPY = b"msr-node-v1"
_N, _R, _P = 2 ** 15, 8, 1
_MAXMEM = 64 * 2 ** 20

# Huellas: 16 bytes de sal + 32 de scrypt, en hex.
_TABLE: tuple[str, ...] = (
    "f01bc9c894e138711d0af08130ccfc744436c4aba264579cdadc8219af24db1aa23df34b412797fbb428bc43896b3e6b",
    "de1555a009c0ed82e899170fb9f92d00df43137e77fc91504a352d8a835d73d8fe1c2c9624fb74e0240cc67656575c91",
    "18bf02056e76ddaa293b5d3e232aeca7b6c5d36a295cc8d185e40ba2bd049d14ebfa188608bc32ea4ea040a7c5a617f1",
    "b51413684ba283b2ef9887c5dc4ecce35868274cec17deea25def3259a37cde8e3cb6d301a4a0431179de2856ee9ea2e",
    "bccd6c253912edfdfb0687f62f3b41783655811c87f982310df35600d2acc693763d6b188be307a8f62b01514aa9adc8",
    "94988598a113852e3973da19af99cd0a0fc4d8e0d02be32fc4868119d240a2af57484e7e7576326b966ef0b340ef41d1",
    "a19f2d0edb0d6e4a860f9481ac7e59edd4ac177b555061cd7566fd8aac1599fedbe092cb86da3298821d764f6cd2b3f7",
    "3063ff03dbd836950ead749ea186b14a8f7bdd13e91bab7dba97b441ee934c15123db5657d582658a76d0471a1802038",
    "6fd9dbe0ee5c4424a39acae933e20df13efbcf57a2ff40282fb5069fa0a68635c51499e1e69430f2261c7a3eb793b539",
    "6bdea6124d88d2f67f280e975797a680f89713c19abab5cf3cbe0966c5b21113c63b6c891204f753aa1154eec3bfcadc",
    "62a0bfb9fc231989e60a98998b4d05bd753d1fff4c2101124bdfb59012b2d201f23a49be77ab2e78f7456b05fc13f11e",
    "bf8059fccfec22ed3c5b02d304be802639fcd66f5ed9c3abe25de997defce1741cfee0a27dff241064f0bd574fd66e99",
    "5db43c5f29e62529445f030f63c165bb00ca89e63d29fb44ba2cc3fdcc19e7c3488d3da4299abeb60e9cf1ca11bdbe93",
    "9acfc22c414cce4d9fe785e9e0a9bd065f929ff2a050945abab3d2591f5950171c0784d89beffa1ec46f54f70be30554",
    "fd4ff4f8fc55aa66f644371ef82b24986e939ae06625992acf8d9fdb0a4d6d1ae5c1f3f04154951e68dfc4b5a1369d36",
    "e4d0dd8f4037e06ed725e486267bdcf5499564e662147fbc3bedfdc294e6e7ed0cdddb588ed65ebedf01d568eb2d1c9d",
    "95a80e912e573731692fb49b365276790eda6326d395e6cbeeb20e663bb1df331cdfccc8fddfa659d9fe3df8d92e855a",
    "292136a4349cc1eb2fd2cca2f15b738111877de2f1c4f3aed3b695bdbb41a2e4730793b43ef05d47ff82bebb0f2e6388",
    "7f8a4e161c2e9a6509c7525dc27c537bcc0c39b1e8788fc02e2d5d6ebcf81797b83c3a28f1ce382d1b017fbe5b2828a2",
    "1525263cf9563159f85c62665bc320780bf8e748cf656976235fc68923bf2ecd5b6a9a53148689429b1c2d714d1f5504",
    "4c8f70169ab2d3ae3078f8973a995c4855e2746e6d718f3a4ecf6d69ee4fbef718c4ba7bdaed5afb474cc045f5bc0c4b",
    "26d9b31a28a40ffdcedf8226015c301afca97d27a81107b14b35a1f685ab9a36128e3b679c056ed6134dceeb68646d04",
    "71ca448872e87cf4f84c07a20e4cc5a4fae17e1d67f93fab2003d5fa6d923304a73d1099f242cdbcaa1a58ee9be91dca",
    "6aebf5d0277eb096271bddf311f11bdfcd1330ed442e226d6651ae9c6784f3c871bfc5d7d7b495d14223b35a15c97a95",
    "dca0d2db3b8aab76ac0c26abec905d4e9f635c328e8785390289c17e4255abffbf074819cd45669725465e04e744a4af",
    "dcbaa1408f2d1c1efecc9bd07597e891ac07785a6cfa265a1eb91c68025824d7f08b856af3341f9b469205f40f7e9000",
    "e1674dbc7f89fdc0a5c760abff4c6e0a811cce084e6eff8ac5c8d367e5150b5cc6983ff1fd1b8d26608113ca96ec78af",
    "b99d80416297a2407662373a00b5804706c590ad1cf8890b77cf1e4bc95a8fd819106cbb1083ce2aa407ed2cc9f61ee4",
    "506395cfffe7eaaea63f4338f90d5cabe119e83297b9c9534a1a5ec82791271ed9de3b4ead433b13b7a2cb96a9be4529",
    "11496b41832f61ab66df2c9458acec95e6691ac6d0501393b4c92dae0c0a8735ec412f816f4d4485ff20300bcc95e91b",
    "82485d8508e29475ab292bfc8d43ea965b2b663c50ef10866b710fd1c2544f9db52eb099f86f94b16de8ba5a09799587",
    "69711104f07e68ad5f490245bd2a36d0bb3839d2a95500f8774ce2ae36f3623f30849d4e061c61a3673d0d446f4404f6",
    "9641198d2dc90eaecd664a2a68bf0650b0a4aa81ca41658840e30bd250d5f84dcc67127a67fe35d5e4d1e6449027fc50",
    "d8a5e507a47bef38fcab3d9164a67dcea7e6330006ac25a27a4998b1bbf9ce4149a46f665ca9efd73e5bf5a3886c2ed1",
    "efc0d5d0ebc151f42f4024e4013cd8513f1e3ade8d5dca42f13d795325f0da755972ad683012c393ee64055ff0b80a91",
    "50e47cd77add230ac2260bfd2d3b392eab27ff191643d535e5813bd6924bfbddc22ad63998f29012b3a0ee6214bfe48b",
    "d24d4c3b16ca198d53a8716f898aab6a9532b3841f3b7475e54a77e9c76f5d89af9ed08d812ad16b58603c2fc2ec2cdd",
    "68011986def645536fb3951c108c823d4c0c1daf3ddee5ce81b5ab4701cb65ddfa0a06740be4651745ba0a8bc02da89b",
    "15dba082713873442cfb37acdd6593fa98072b53408fb96797b35abd3b299d76be45bcb435cfa20e2a5bd043a9aa51e0",
    "a96ff05ec989c0f35d8cbe67be1861c887780750501c5401653d9161dbf91a8305e2a6c093fe7afabc0bffdf4802048e",
    "f587cde5789ffd27530ec911c39db318c366a028a1c56b45daeb5f53c9e7927766b10b19620ea8485b179c528484d345",
    "ddd9caf948e990fabf9b6cd597e15fe12e0919274bd16f7d2ee2fa91a257c4c2991fb28d9963d60d80d7bdf6d9e68ff1",
    "f2c1e2e23cb5d3212e440423c1b7cd25aaa645756abb9609d2d2b3202754b51dcb920caef9c5764761354912f6eef734",
    "c3ed96de2ab1fc503edaecd811e5f035a8d8a8caab1ea2b3bc9272e4189397f572dcda62303210d8d95356f3813b84e3",
    "75fae6c7f4edb74867a3b98af958f47aef2fa25542c501c851da0b3828c3feb2c261b5b63d1d5a34c5f5d201896818af",
    "d0f4faf6e322f96be98cdb5df62eb99f24e4a9494896c2fbd8dc07b9762c71664ce6ed9510858f2cc9e7fd46dc7172e4",
    "222a24cf76b9b8b4d86104af85a983b9c91bb8076a7db7903759b369640750254eb5a1a60aa8b00d4ee7a5cde90a3709",
    "8632ff0253a4884832a214c5530eb46e77da0448b7f61ab3f1679c7eeb6d84826de4a9440f3c6db772a97a2e65f7d9c2",
    "7a7f47e83c9c740cbac8fbf0ae81ed4214ca0c04bac9711dc55bd9787345e5bfa3ccff381ac8ef6829b492a53a151ed0",
    "5c8cc6c0769e15724c065b8a68b97500572eef937aa4df9ca231232fa465d937bf3e008567d6d55f1d800945c3e6f867",
    "49ea6bf408ef6a86ef08d18f03ff57b0027e1ae9ac8a6f59c49605c16d1d5a5d2c46633cbb81ad2b4b4ef47649e0d4e8",
    "a82d3340bf19a694a2742caaaf14f2245c9376a9a0375895beb0e3f0cbdd17fbb3f889df6092223604aa0a8404a36d3c",
    "7cf6763c1297b954f3c87e3cb0fe419346a6ce8911cca138aefa58fb35a2fdca8a81d6256848ed79a57c6199648968cb",
    "a8b2deb04a9198ec2f1b5fe7a8797ce83f0cbc9824675783b974473c8a13b9acf4add412bcece90b14ef118fac211943",
    "16a68dc387e9e7d69047f14c583f1e11c756e0a60f1d9b12faeeaeda5aef192239409005b026b07df5199466f8e01b6a",
    "bbfec132aae21a2ea39e45dc1edcc03d03e038f9023479752a93edced2e7b14bbee1124e3eaca688bafe978f436e066e",
    "5d4ecacd47bb3af55c7a1d0e0a828eb0d50cc8041e7c7187e687a1248d35cd84e22609010fe5ed78c16111aaabf576e5",
    "50db7fbe22d4a295d605884022ed90fd7b18efcfbc3c6e831f920ccfca9656811bf6466c099e157efc6568830d175cca",
    "41374aaa629431bd4823d33ebe4f91f979db25599f0d344ff38379d5ba6aab5016530916923f0ceb3f8149a5015b9efc",
    "3f8bbd2fa000eb1ff36860f0233a0838dbb3fe4c270a89bf743890b090a5630e5f9ccec73a408ae016386bc7ef76a685",
)


def normalize(code: str) -> str:
    """Mayúsculas, sin espacios ni guiones."""
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def _matches(code: str, row: str) -> bool:
    salt, want = bytes.fromhex(row[:32]), bytes.fromhex(row[32:])
    got = hashlib.scrypt(code.encode(), salt=salt, n=_N, r=_R, p=_P,
                         maxmem=_MAXMEM, dklen=len(want))
    return hmac.compare_digest(got, want)


def check(code: str, now: datetime | None = None,
          table: tuple[str, ...] | None = None) -> bool:
    """True si el código es válido ahora (con un margen de ±1)."""
    code = normalize(code)
    table = table or _TABLE
    if len(code) != CODE_LEN or not table:
        return False
    slot = (now or datetime.now()).minute
    for d in (0, -1, 1):
        if _matches(code, table[(slot + d) % len(table)]):
            return True
    return False


def status_line(now: datetime | None = None) -> str:
    """Línea pequeña que se muestra encima del campo del código."""
    now = now or datetime.now()
    return f"{now:%d/%m/%Y %H:%M} · No tienes activada la contraseña de los servidores."


# --- Activación del equipo (DPAPI, sin pywin32: vale dentro de los .exe) -----
def _blob_cls():
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    return ctypes, Blob


def _dpapi(data: bytes, protect: bool) -> bytes:
    ctypes, Blob = _blob_cls()
    buf = ctypes.create_string_buffer(data, len(data))
    ent = ctypes.create_string_buffer(_ENTROPY, len(_ENTROPY))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    extra = Blob(len(_ENTROPY), ctypes.cast(ent, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN (nunca muestra diálogos).
    if not fn(ctypes.byref(src), None, ctypes.byref(extra), None, None, 0x1,
              ctypes.byref(out)):
        raise OSError("DPAPI falló")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _host() -> str:
    return f"{socket.gethostname()}|{os.environ.get('USERNAME', '')}".lower()


def is_activated(state_dir: str | os.PathLike) -> bool:
    """True si este equipo y usuario ya metieron un código válido."""
    path = Path(state_dir) / ACCESS_FILE
    try:
        data = json.loads(_dpapi(path.read_bytes(), protect=False).decode("utf-8"))
    except Exception:
        return False
    return isinstance(data, dict) and data.get("host") == _host()


def activate(state_dir: str | os.PathLike) -> None:
    """Deja el equipo activado (tras aceptar un código)."""
    path = Path(state_dir) / ACCESS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"host": _host(), "at": datetime.now().isoformat(timespec="seconds")}
    path.write_bytes(_dpapi(json.dumps(payload).encode("utf-8"), protect=True))
