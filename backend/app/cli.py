import sys
import time
import logging
from app.services.enrichment_worker import EnrichmentWorker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_enrichment_worker():
    logger.info("Starting enrichment worker...")
    worker = EnrichmentWorker(max_attempts=3, claim_batch_size=10)
    while True:
        try:
            worker.run()
        except Exception as e:
            logger.error(f"Worker iteration failed: {e}")
        time.sleep(10)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "enrichment-worker":
        run_enrichment_worker()
    else:
        print("Unknown command")
