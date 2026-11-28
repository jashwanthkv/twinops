"""
client/integrations/__init__.py — Integrations package exports.
"""
from client.integrations.payment_gateway import payment_gateway, ExternalPaymentGatewayClient

__all__ = ["payment_gateway", "ExternalPaymentGatewayClient"]
