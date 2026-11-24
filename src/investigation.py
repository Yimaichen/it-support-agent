"""Canonical scope normalization for status queries."""


def effective_status_region(service, requested: str | None) -> str | None:
    """One scope rule for querying and authorizing a service observation."""
    if not service.region_status:
        return None
    return next(
        (
            region
            for region in service.region_status
            if region.casefold() == (requested or "").strip().casefold()
        ),
        requested.strip() if requested else None,
    )
