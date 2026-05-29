"""PLM->ERP publication outbox (G2 R2 implementation).

The outbound publication seam: a durable outbox + an adapter interface that
consumes the R1-B publication-readiness verdict. No real ERP connector, no Odoo
runtime, no external write (the only in-repo adapter is a no-I/O Null adapter).
See docs/DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R2_BUILD_TASKBOOK_20260528.md.
"""
