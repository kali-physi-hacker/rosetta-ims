#!/usr/bin/env python
"""
Simple test to verify CatalogueIngestionRun model works correctly.
Run this directly with: python test_ingestion_run_simple.py
"""

import sys
import os
from datetime import datetime, timezone
import json

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import database setup
from database import Base, SessionLocal, engine, run_migrations

# Import the canonical models used by the application.
from models import CatalogueImport, CatalogueIngestionRun, CatalogueItem, Supplier


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
        # Create a supplier
        supplier = Supplier(
            code="TEST",
            name="Test Supplier",
            created_at=datetime.now(timezone.utc).isoformat()
        )
        session.add(supplier)
        session.commit()
        print(f"   Created supplier: {supplier.name}")

        # Create a catalogue import (source asset)
        catalogue_import = CatalogueImport(
            supplier_id=supplier.id,
            filename="test_catalogue.pdf",
            format="pdf",
            imported_at=datetime.now(timezone.utc).isoformat(),
            status="pending",
            source_ref="s3://bucket/test_catalogue.pdf"
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
            started_at=datetime.now(timezone.utc).isoformat(),
            created_at=datetime.now(timezone.utc).isoformat(),
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
            started_at=datetime.now(timezone.utc).isoformat(),
            created_at=datetime.now(timezone.utc).isoformat(),
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
            started_at=datetime.now(timezone.utc).isoformat(),
            created_at=datetime.now(timezone.utc).isoformat()
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
                started_at=datetime.now(timezone.utc).isoformat(),
                created_at=datetime.now(timezone.utc).isoformat()
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
            created_at=datetime.now(timezone.utc).isoformat()
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

    finally:
        session.close()


def test_error_tracking(source_asset_id):
    """Test error tracking for failed runs."""
    print("\n5. Testing error tracking...")

    session = SessionLocal()
    try:
        error_details = {
            "stack_trace": "Traceback...",
            "pdf_page": 5,
            "extraction_step": "table_parsing",
            "retry_count": 2
        }

        run = CatalogueIngestionRun(
            source_asset_id=source_asset_id,
            status="failed",
            started_at="2024-01-01T10:00:00",
            completed_at="2024-01-01T10:02:00",
            error_type="extraction_failure",
            error_message="Unable to parse table on page 5",
            error_details=json.dumps(error_details),
            created_at=datetime.now(timezone.utc).isoformat()
        )

        session.add(run)
        session.commit()
        print(f"   Created failed run with error tracking, ID: {run.id}")

        # Verify error information
        saved_run = session.query(CatalogueIngestionRun).filter_by(id=run.id).first()
        assert saved_run.status == "failed"
        assert saved_run.error_type == "extraction_failure"
        assert "page 5" in saved_run.error_message

        saved_details = json.loads(saved_run.error_details)
        assert saved_details["pdf_page"] == 5
        assert saved_details["retry_count"] == 2
        print("   ✅ Error tracking test passed!")

    finally:
        session.close()


def test_version_tracking(source_asset_id):
    """Test version tracking across reprocessing runs."""
    print("\n6. Testing version tracking...")

    session = SessionLocal()
    try:
        # Run 1: Initial version
        run1 = CatalogueIngestionRun(
            source_asset_id=source_asset_id,
            extraction_profile_id="supplier-alfamedic",
            extraction_profile_version="1.0.0",
            extractor_name="claude-haiku",
            extractor_version="3.0-20240101",
            status="completed",
            started_at=datetime.now(timezone.utc).isoformat(),
            created_at=datetime.now(timezone.utc).isoformat()
        )
        session.add(run1)
        session.commit()

        # Run 2: Updated profile version
        run2 = CatalogueIngestionRun(
            source_asset_id=source_asset_id,
            parent_run_id=run1.id,
            extraction_profile_id="supplier-alfamedic",
            extraction_profile_version="1.1.0",  # Updated
            extractor_name="claude-haiku",
            extractor_version="3.0-20240101",
            status="completed",
            started_at=datetime.now(timezone.utc).isoformat(),
            created_at=datetime.now(timezone.utc).isoformat()
        )
        session.add(run2)
        session.commit()

        # Run 3: Updated extractor version
        run3 = CatalogueIngestionRun(
            source_asset_id=source_asset_id,
            parent_run_id=run2.id,
            extraction_profile_id="supplier-alfamedic",
            extraction_profile_version="1.1.0",
            extractor_name="claude-haiku",
            extractor_version="4.5-20251001",  # Updated
            status="completed",
            started_at=datetime.now(timezone.utc).isoformat(),
            created_at=datetime.now(timezone.utc).isoformat()
        )
        session.add(run3)
        session.commit()

        print(f"   Created version progression: {run1.id} -> {run2.id} -> {run3.id}")

        # Verify version progression
        assert run1.extraction_profile_version == "1.0.0"
        assert run2.extraction_profile_version == "1.1.0"
        assert run2.extractor_version == "3.0-20240101"
        assert run3.extractor_version == "4.5-20251001"
        print("   ✅ Version tracking test passed!")

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
        test_error_tracking(source_asset_id)
        test_version_tracking(source_asset_id)

        print("\n" + "="*60)
        print("✅ All tests passed successfully!")
        print("="*60)

        # Print summary
        session = SessionLocal()
        try:
            total_runs = session.query(CatalogueIngestionRun).count()
            total_imports = session.query(CatalogueImport).count()
            print(f"\nDatabase Summary:")
            print(f"  Total Ingestion Runs: {total_runs}")
            print(f"  Total Catalogue Imports: {total_imports}")
        finally:
            session.close()

    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
