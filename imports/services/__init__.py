"""
Import services for IT Glue, Hudu, MagicPlan, and CSV
"""
from .base import BaseImportService
from .itglue import ITGlueImportService
from .hudu import HuduImportService
from .magicplan import MagicPlanImportService
from .csv_importer import CSVImportService


def get_import_service(import_job):
    """
    Get the appropriate import service for an import job.
    """
    if import_job.source_type == 'itglue':
        return ITGlueImportService(import_job)
    elif import_job.source_type == 'hudu':
        return HuduImportService(import_job)
    elif import_job.source_type == 'magicplan':
        return MagicPlanImportService(import_job)
    elif import_job.source_type == 'csv':
        return CSVImportService(import_job)
    else:
        raise ValueError(f"Unknown source type: {import_job.source_type}")


__all__ = [
    'BaseImportService',
    'ITGlueImportService',
    'HuduImportService',
    'MagicPlanImportService',
    'CSVImportService',
    'get_import_service',
]
