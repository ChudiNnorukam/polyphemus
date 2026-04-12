import time
import base64
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding


class KalshiAuth:
    """Kalshi RSA-PSS request signer."""

    def __init__(self, key_id: str, private_key_path: str):
        self.key_id = key_id
        with open(private_key_path, "rb") as f:
            self.private_key = serialization.load_pem_private_key(f.read(), password=None)

    def headers(self, method: str, path: str) -> dict:
        """Generate auth headers for a request.

        Args:
            method: HTTP method (GET, POST, DELETE)
            path: URL path only (no query string, no host)
        """
        ts = str(int(time.time() * 1000))
        msg = f"{ts}{method}{path}".encode()
        sig = self.private_key.sign(
            msg,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }
