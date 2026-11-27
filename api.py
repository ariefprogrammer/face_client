import requests


class ApiError(Exception):
    pass


class NetworkError(ApiError):
    pass


class AuthError(ApiError):
    pass


class RejectedError(ApiError):
    pass


class ApiClient:
    def __init__(self, base_url, token, timeout=10):
        self.base = f"{base_url}/api/v1/client"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })

    def _json(self, method, path, **kwargs):
        try:
            res = self.session.request(
                method, f"{self.base}/{path}", timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise NetworkError(f"Server tidak terjangkau ({exc.__class__.__name__})") from exc

        if res.status_code == 401:
            raise AuthError("Token perangkat ditolak server")
        if res.status_code == 429 or res.status_code >= 500:
            raise NetworkError(f"Server sibuk atau error ({res.status_code})")
        if res.status_code >= 400:
            try:
                message = res.json().get("message", res.text[:200])
            except ValueError:
                message = res.text[:200]
            raise RejectedError(f"{res.status_code}: {message}")

        return res.json()

    def get_config(self):
        return self._json("GET", "config")

    def get_encodings(self, updated_since=None):
        params = {"updated_since": updated_since} if updated_since else None
        return self._json("GET", "encodings", params=params)

    def heartbeat(self, version):
        return self._json("POST", "heartbeat", data={"client_version": version})

    def send_attendance(self, entry, photo_path):
        with open(photo_path, "rb") as photo:
            return self._json(
                "POST",
                "attendance",
                data={
                    "client_event_id": entry["client_event_id"],
                    "employee_id": entry["employee_id"],
                    "scanned_at": entry["scanned_at"],
                    "score": entry["score"],
                },
                files={"photo": ("capture.jpg", photo, "image/jpeg")},
            )

    def enroll(self, code, samples):
        return self._json("POST", "enroll", json={"code": code, "samples": samples})