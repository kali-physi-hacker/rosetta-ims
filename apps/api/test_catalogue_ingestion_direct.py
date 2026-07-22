#!/usr/bin/env python
"""
Direct test for CatalogueIngestionRun model using the existing models.py file.
Run this directly with: python test_catalogue_ingestion_direct.py
"""

import sys
import os
from datetime import datetime, timezone
import json

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import from the existing models.py that has CatalogueIngestionRun
from models import (
    CatalogueImport,
    CatalogueIngestionRun,
    CatalogueItem,
    Supplier
)
from database import Base, SessionLocal, engine, run_migrations


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def setup_database():
    """Create all tables in the database."""
    print("Setting up database tables...")
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    print("Database tables created successfully!")


def test_basic_ingestion_run():
    """Test creating a basic ingestion run."""
    print("\n1. Testing basic ingestion run creation...")

    session = SessionLocal()
    try:
        # Create a supplier with unique code using timestamp
        import time
        unique_code = f"TEST_ING_{int(time.time())}"
        supplier = Supplier(
            code=unique_code,
            name="Test Ingestion Supplier",
            created_at=utc_now()
        )
        session.add(supplier)
        session.commit()
        print(f"   Created supplier: {supplier.name}")

        # Create a catalogue import (source asset)
        catalogue_import = CatalogueImport(
            supplier_id=supplier.id,
            filename="test_ingestion_catalogue.pdf",
            format="pdf",
            imported_at=utc_now(),
            status="pending",
            source_ref="s3://bucket/test_ingestion_catalogue.pdf"
        )
        session.add(catalogue_import)
        session.commit()
        print(f"   Created catalogue import: {catalogue_import.filename}")

        # Create an ingestion run
        run = CatalogueIngestionRun(
            source_asset_id=catalogue_import.id,
            supplier_id=supplier.id,
            extraction_profile_id="alfamedic-v1",
            extraction_profile_version="1.2.0",
            extractor_name="claude-haiku",
            extractor_version="4.5-20251001",
            status="pending",
            started_at=utc_now(),
            created_at=utc_now(),
            created_by="test_script"
        )
        session.add(run)
        session.commit()
        print(f"   Created ingestion run with ID: {run.id}")

        # Verify the run
        saved_run = session.query(CatalogueIngestionRun).filter_by(id=run.id).first()
        assert saved_run is not None, "Run not found in database"
        assert saved_run.extraction_profile_id == "alfamedic-v1"
        assert saved_run.status == "pending"
        print("   ✅ Basic ingestion run test passed!")

        return saved_run.id, catalogue_import.id, supplier.id

    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def test_parent_child_relationship(source_asset_id):
    """Test parent-child run relationships."""
    print("\n2. Testing parent-child run relationships...")

    session = SessionLocal()
    try:
        # Create parent run
        parent_run = CatalogueIngestionRun(
            source_asset_id=source_asset_id,
            status="failed",
            started_at=utc_now(),
            created_at=utc_now(),
            error_type="extraction_failure",
            error_message="Failed to parse PDF"
        )
        session.add(parent_run)
        session.commit()
        print(f"   Created parent run (failed) with ID: {parent_run.id}")

        # Create child run (retry)
        child_run = CatalogueIngestionRun(
            source_asset_id=source_asset_id,
            parent_run_id=parent_run.id,
            status="completed",
            started_at=utc_now(),
            created_at=utc_now()
        )
        session.add(child_run)
        session.commit()
        print(f"   Created child run (retry) with ID: {child_run.id}")

        # Verify relationships
        assert child_run.parent_run_id == parent_run.id
        assert child_run.parent_run.id == parent_run.id
        assert len(parent_run.child_runs) == 1
        assert parent_run.child_runs[0].id == child_run.id
        print("   ✅ Parent-child relationship test passed!")

        return parent_run.id, child_run.id

    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def test_multiple_runs_same_asset(source_asset_id):
    """Test multiple runs for the same source asset."""
    print("\n3. Testing multiple runs for same asset...")

    session = SessionLocal()
    try:
        runs = []
        statuses = ["completed", "failed", "completed", "running"]

        for i, status in enumerate(statuses):
            run = CatalogueIngestionRun(
                source_asset_id=source_asset_id,
                status=status,
                extraction_profile_version=f"1.{i}.0",
                started_at=utc_now(),
                created_at=utc_now()
            )
            session.add(run)
            runs.append(run)

        session.commit()
        print(f"   Created {len(runs)} runs for the same asset")

        # Query all runs for this asset
        all_runs = session.query(CatalogueIngestionRun).filter_by(
            source_asset_id=source_asset_id
        ).all()

        # Should have the 4 we just created plus any from previous tests
        assert len(all_runs) >= 4
        print(f"   Total runs for this asset: {len(all_runs)}")
        print("   ✅ Multiple runs test passed!")

    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def test_operational_metrics(source_asset_id):
    """Test storing operational metrics."""
    print("\n4. Testing operational metrics...")

    session = SessionLocal()
    try:
        confidence_metrics = {
            "average": 0.85,
            "min": 0.65,
            "max": 0.98,
            "distribution": {
                "high": 45,
                "medium": 30,
                "low": 5
            }
        }

        run = CatalogueIngestionRun(
            source_asset_id=source_asset_id,
            status="completed",
            started_at="2024-01-01T10:00:00",
            completed_at="2024-01-01T10:05:30",
            items_extracted=150,
            extraction_duration_ms=330000,
            confidence_metrics=json.dumps(confidence_metrics),
            created_at=utc_now()
        )

        session.add(run)
        session.commit()
        print(f"   Created run with metrics, ID: {run.id}")

        # Verify metrics
        saved_run = session.query(CatalogueIngestionRun).filter_by(id=run.id).first()
        assert saved_run.items_extracted == 150
        assert saved_run.extraction_duration_ms == 330000

        saved_metrics = json.loads(saved_run.confidence_metrics)
        assert saved_metrics["average"] == 0.85
        assert saved_metrics["distribution"]["high"] == 45
        print("   ✅ Operational metrics test passed!")

    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def test_relationships_with_catalogue_items(source_asset_id):
    """Test relationships between ingestion run and catalogue items."""
    print("\n5. Testing relationship with catalogue items...")

    session = SessionLocal()
    try:
        # Create a run
        run = CatalogueIngestionRun(
            source_asset_id=source_asset_id,
            status="completed",
            started_at=utc_now(),
            created_at=utc_now(),
            items_extracted=3
        )
        session.add(run)
        session.commit()
        print(f"   Created run with ID: {run.id}")

        # Create catalogue items for this run
        items = []
        for i in range(3):
            item = CatalogueItem(
                import_id=source_asset_id,
                ingestion_run_id=run.id,
                raw_description=f"Test Product {i}",
                supplier_sku=f"SKU-{i}",
                cost_price=100.0 + i * 10,
                created_at=utc_now(),
                review_status="pending"
            )
            session.add(item)
            items.append(item)

        session.commit()
        print(f"   Created {len(items)} catalogue items")

        # Verify relationships
        assert len(run.items) == 3
        for i, item in enumerate(run.items):
            assert item.raw_description == f"Test Product {i}"
            assert item.ingestion_run.id == run.id
        print("   ✅ Catalogue items relationship test passed!")

    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def test_summary():
    """Print summary of all data in the database."""
    print("\n" + "="*60)
    print("Test Summary")
    print("="*60)

    session = SessionLocal()
    try:
        total_runs = session.query(CatalogueIngestionRun).count()
        total_imports = session.query(CatalogueImport).count()
        total_items = session.query(CatalogueItem).filter(
            CatalogueItem.ingestion_run_id.isnot(None)
        ).count()

        # Get run statistics
        completed = session.query(CatalogueIngestionRun).filter_by(status="completed").count()
        failed = session.query(CatalogueIngestionRun).filter_by(status="failed").count()
        pending = session.query(CatalogueIngestionRun).filter_by(status="pending").count()
        running = session.query(CatalogueIngestionRun).filter_by(status="running").count()

        # Get runs with parent
        with_parent = session.query(CatalogueIngestionRun).filter(
            CatalogueIngestionRun.parent_run_id.isnot(None)
        ).count()

        print(f"Database Statistics:")
        print(f"  Total Ingestion Runs: {total_runs}")
        print(f"    - Completed: {completed}")
        print(f"    - Failed: {failed}")
        print(f"    - Pending: {pending}")
        print(f"    - Running: {running}")
        print(f"  Runs with parent (retries): {with_parent}")
        print(f"  Total Catalogue Imports: {total_imports}")
        print(f"  Total Items with run ID: {total_items}")

    finally:
        session.close()


def main():
    """Run all tests."""
    print("="*60)
    print("Testing CatalogueIngestionRun Model")
    print("="*60)

    try:
        # Setup database
        setup_database()

        # Run tests
        run_id, source_asset_id, supplier_id = test_basic_ingestion_run()
        test_parent_child_relationship(source_asset_id)
        test_multiple_runs_same_asset(source_asset_id)
        test_operational_metrics(source_asset_id)
        test_relationships_with_catalogue_items(source_asset_id)

        # Print summary
        test_summary()

        print("\n✅ All tests passed successfully!")

    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
