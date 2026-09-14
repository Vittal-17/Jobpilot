"""backfill canonical identity

Revision ID: f25ff86125c9
Revises: b64748522884
Create Date: 2026-09-14 19:34:33.053822

"""
from typing import Sequence, Union
import hashlib
import re
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f25ff86125c9'
down_revision: Union[str, Sequence[str], None] = 'b64748522884'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _local_normalize_text(text: str | None) -> str:
    if not text:
        return ""
    text = str(text).lower()
    text = re.sub(r'[^a-z0-9]', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def _local_normalize_url(url_str: str | None) -> str | None:
    if not url_str:
        return None
    lower_url = url_str.lower()
    if "adzuna.com" in lower_url or "jooble.org" in lower_url:
        return None
    try:
        parsed = urlparse(url_str)
        if not parsed.scheme or not parsed.netloc:
            return None
        q_params = parse_qsl(parsed.query, keep_blank_values=True)
        filtered_params = [
            (k, v) for k, v in q_params
            if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'fbclid', 'msclkid', 'mc_eid')
        ]
        filtered_params.sort(key=lambda x: (x[0], x[1]))
        new_query = urlencode(filtered_params)
        clean_url = urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip('/') if parsed.path != '/' else '/',
            parsed.params,
            new_query,
            ''
        ))
        return clean_url
    except Exception:
        return None


def _local_compute_canonical_hash(url: str | None, company: str | None, title: str | None, location: str | None) -> str | None:
    url_norm = _local_normalize_url(url)
    if url_norm:
        return hashlib.sha256(f"URL|{url_norm}".encode('utf-8')).hexdigest()

    company_norm = _local_normalize_text(company)
    title_norm = _local_normalize_text(title)
    loc_norm = _local_normalize_text(location)

    if len(company_norm) >= 2 and len(title_norm) >= 2 and len(loc_norm) >= 2:
        identity_str = f"ETL|{company_norm}|{title_norm}|{loc_norm}"
        return hashlib.sha256(identity_str.encode('utf-8')).hexdigest()

    return None


def upgrade() -> None:
    bind = op.get_bind()

    # Fetch all existing jobs
    jobs = bind.execute(sa.text("SELECT id, title, company, location, source, source_job_id, url, canonical_hash FROM jobs")).fetchall()

    existing_hashes = set(row.canonical_hash for row in jobs if row.canonical_hash is not None)

    for job in jobs:
        # Backfill job_sources entry if missing
        js_exists = bind.execute(
            sa.text("SELECT 1 FROM job_sources WHERE source = :s AND source_job_id = :sjid"),
            {"s": job.source, "sjid": job.source_job_id}
        ).fetchone()
        if not js_exists:
            bind.execute(
                sa.text("INSERT INTO job_sources (job_id, source, source_job_id, url) VALUES (:jid, :s, :sjid, :url)"),
                {"jid": job.id, "s": job.source, "sjid": job.source_job_id, "url": job.url}
            )

        # Compute canonical hash if missing
        if job.canonical_hash is None:
            chash = _local_compute_canonical_hash(job.url, job.company, job.title, job.location)
            if chash and chash not in existing_hashes:
                bind.execute(
                    sa.text("UPDATE jobs SET canonical_hash = :chash WHERE id = :jid"),
                    {"chash": chash, "jid": job.id}
                )
                existing_hashes.add(chash)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE jobs SET canonical_hash = NULL"))
    bind.execute(sa.text("DELETE FROM job_sources"))
