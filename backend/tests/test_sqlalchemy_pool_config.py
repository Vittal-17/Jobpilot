from app.db.database import engine

def test_sqlalchemy_pool_limits_applied():
    # Assert that explicit bounds match the internal architecture limits
    assert engine.pool.size() == 20
    # QueuePool exposes max_overflow as _max_overflow
    assert engine.pool._max_overflow == 25
