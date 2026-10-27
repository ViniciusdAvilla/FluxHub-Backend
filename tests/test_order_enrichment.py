"""Regression guards for imported order items and privacy-safe buyer storage."""
from app.database.models import (
    MercadoLivreBuyerOrderModel,
    MercadoLivreOrderLineModel,
    MercadoLivreListingPhotoModel,
    MercadoLivreBuyerAccessAuditModel,
)
from app.routers.analytics import safe_public_thumbnail, thumbnail_from_item


def test_only_public_mercado_livre_images_are_accepted():
    assert safe_public_thumbnail("https://http2.mlstatic.com/D_12345.jpg")
    assert safe_public_thumbnail("http://http2.mlstatic.com/D_12345.jpg") is None
    assert safe_public_thumbnail("https://example.com/private") is None
    assert safe_public_thumbnail("javascript:alert(1)") is None
    assert thumbnail_from_item({
        "secure_thumbnail": "https://http2.mlstatic.com/D_12345.jpg",
    }) == "https://http2.mlstatic.com/D_12345.jpg"


def test_buyer_indexes_store_no_pii():
    names = {column.name for column in MercadoLivreBuyerOrderModel.__table__.columns}
    assert {"user_id", "mercado_livre_user_id", "order_external_id", "buyer_id"} <= names
    assert not names.intersection({
        "email", "phone", "cpf", "document_number", "address", "full_name",
    })


def test_audit_retains_no_private_fields():
    fields = {column.name for column in MercadoLivreBuyerAccessAuditModel.__table__.columns}
    assert fields == {"id", "user_id", "mercado_livre_user_id",
                      "order_external_id", "accessed_at"}


def test_order_line_and_photo_are_tenant_scoped():
    for model in (MercadoLivreOrderLineModel, MercadoLivreListingPhotoModel):
        names = {column.name for column in model.__table__.columns}
        assert {"user_id", "mercado_livre_user_id"} <= names
        assert any("user_id" in {column.name for column in constraint.columns}
                   for constraint in model.__table__.constraints
                   if hasattr(constraint, "columns"))
