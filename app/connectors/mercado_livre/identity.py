"""Safe identity fields for a linked Mercado Livre seller (no credentials/PII)."""
from urllib.parse import urlsplit, urlunsplit


def safe_ml_image_url(value: object) -> str | None:
    """Only serve external images from Mercado Livre's public image CDN."""
    if not isinstance(value, str) or len(value) > 2048:
        return None
    try:
        uri = urlsplit(value.strip())
        hostname = (uri.hostname or "").lower()
        if uri.scheme not in ("http", "https") or (
            hostname != "mlstatic.com" and not hostname.endswith(".mlstatic.com")
        ):
            return None
        if uri.username or uri.password or uri.port or not uri.path.startswith("/"):
            return None
        # The ML API sometimes returns http; use the same trusted CDN over HTTPS.
        return urlunsplit(("https", uri.netloc, uri.path, uri.query, ""))
    except ValueError:
        return None


def normalized_seller_identity(profile: dict, account_id: int) -> dict:
    nickname = str(profile.get("nickname") or "").strip()[:150]
    company = profile.get("company")
    brand = (company.get("brand_name") if isinstance(company, dict) else None)
    full_name = " ".join(
        str(value).strip() for value in (
            profile.get("first_name"), profile.get("last_name")
        ) if isinstance(value, str) and value.strip()
    )
    display_name = (
        str(brand).strip() if isinstance(brand, str) and brand.strip()
        else full_name or nickname or f"Vendedor {account_id}"
    )[:150]
    picture_url = next(
        (picture for key in ("logo", "picture", "picture_url")
         if (picture := safe_ml_image_url(profile.get(key)))), None
    )
    return {
        "account_id": account_id,
        "nickname": nickname or f"Vendedor {account_id}",
        "display_name": display_name,
        "picture_url": picture_url,
    }
