import sys
import time
import logging
from app.services.enrichment_worker import EnrichmentWorker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _touch_heartbeat():
    try:
        with open('/tmp/worker_heartbeat', 'w') as f:
            f.write(str(time.time()))
    except Exception as e:
        logger.error(f"Failed to write heartbeat: {e}")

class HeartbeatingEnrichmentWorker(EnrichmentWorker):
    def process_job(self, db, job_id, url, token, source_execution_id=None):
        _touch_heartbeat()
        super().process_job(db, job_id, url, token, source_execution_id)
        _touch_heartbeat()

def run_enrichment_worker():
    logger.info("Starting enrichment worker...")
    _touch_heartbeat()
    worker = HeartbeatingEnrichmentWorker(max_attempts=3, claim_batch_size=10)
    while True:
        _touch_heartbeat()
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
