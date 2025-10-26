"""
Document Pydantic schemas
"""
from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class DocumentBase(BaseModel):
    """Base document schema"""
    file_name: str
    fund_id: Optional[int] = None


class DocumentCreate(DocumentBase):
    """Document creation schema"""
    file_path: str


class DocumentUpdate(BaseModel):
    """Document update schema"""
    parsing_status: Optional[str] = None
    error_message: Optional[str] = None


class Document(DocumentBase):
    """Document response schema"""
    id: int
    file_path: Optional[str] = None
    upload_date: datetime
    parsing_status: str
    error_message: Optional[str] = None
    
    class Config:
        from_attributes = True


class DocumentStatus(BaseModel):
    """Document parsing status"""
    document_id: int
    status: str
    progress: Optional[float] = None
    error_message: Optional[str] = None
    stats: Optional[dict] = None


class DocumentUploadResponse(BaseModel):
    """Document upload response"""
    document_id: int
    task_id: Optional[str] = None
    status: str
    message: str
