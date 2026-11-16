"""Real PDF/HTTP/migrations/pgvector chain. Vectors deterministic; no LLM."""
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import secrets
from alembic import command
from alembic.config import Config
import pymupdf
import pytest
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.documents import router, rag_service
from app.documents.models import Document, DocumentRevisionLog
from app.documents.audience_service import get_user_context
from app.models import Permission, RolePermission
from app.runtime_security import restrict_runtime_engine
from app.services.authorization import has_permission
from document_test_infrastructure import fresh_db
from test_authentication_postgres import isolated_postgres
from test_usage import usage
pytestmark=pytest.mark.integration

def test_pdf_upload_review_correct_publish_retrieve_and_revoke(fresh_db,usage,tmp_path,monkeypatch):
    central,client,headers=usage
    with Session(central) as s,s.begin():
        for code in ('documents:upload','documents:update','documents:archive'):
            s.add(Permission(id=code,code=code));s.flush()
            s.add(RolePermission(role_id='administrator',permission_id=code))
    owner=create_engine(fresh_db['url'],hide_parameters=True)
    cfg=Config(str(Path(__file__).parents[1]/'tenant_alembic.ini'))
    with owner.begin() as c:
        cfg.attributes['connection']=c;command.upgrade(cfg,'head')
    role='pipeline_'+secrets.token_hex(6)
    password=secrets.token_urlsafe(32)
    with owner.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
        pg=c.connection.driver_connection
        pg.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
        pg.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(fresh_db['db_name'])))
        pg.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        pg.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(sql.Identifier(fresh_db['db_name']),sql.Identifier(role)))
        pg.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(role)))
        pg.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE ON documents,document_versions TO {}').format(sql.Identifier(role)))
        pg.execute(sql.SQL('GRANT SELECT,INSERT ON document_chunks,document_audit_logs,document_revision_logs TO {}').format(sql.Identifier(role)))
        pg.execute(sql.SQL('GRANT SELECT,INSERT,DELETE ON document_audiences TO {}').format(sql.Identifier(role)))
        pg.execute(sql.SQL('GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}').format(sql.Identifier(role)))
    runtime=restrict_runtime_engine(create_engine(fresh_db['url'].set(username=role,password=password),hide_parameters=True))
    @contextmanager
    def tenant(user_id,department_id,permission_code):
        with Session(central) as s:
            assert has_permission(s,user_id,permission_code,department_id)
        assert department_id=='hr'
        with Session(runtime) as s: yield s
    monkeypatch.setattr(router,'_open_department_session',tenant)
    monkeypatch.setattr(router,'UPLOAD_DIR',str(tmp_path))
    def no_llm(*a,**kw): raise AssertionError('LLM must not be invoked')
    monkeypatch.setattr(router.provider_accounting,'generate',no_llm)
    pdf=pymupdf.open();page=pdf.new_page()
    page.insert_text((50,70),'SYNTHETIC POLICY: Annual leave is 30 days for eligible employees.')
    original=pdf.tobytes();pdf.close()
    r=client.post('/api/documents/upload',headers=headers['admin'],data={'department_id':'hr'},
        files={'file':('synthetic.pdf',original,'application/pdf')})
    assert r.status_code==200,r.text
    identity=r.json()['document_id'];review=f'/api/documents/{identity}/review?department_id=hr'
    assert client.get(review,headers=headers['employee']).status_code==403
    r=client.get(review,headers=headers['admin']);assert r.status_code==200,r.text
    assert '30 days' in r.json()['extracted_text'] and r.json()['status']=='pending_review'
    corrected='سياسة اصطناعية: الإجازة السنوية ثلاثون يوماً للموظفين المستحقين.'
    r=client.post(f'/api/documents/{identity}/revise?department_id=hr',headers=headers['admin'],json={'expected_version':1,'text':corrected})
    assert r.status_code==200,r.text
    assert client.get(review,headers=headers['admin']).json()['extracted_text']==corrected
    with Session(runtime) as s:
        assert rag_service.retrieve_relevant_chunks(s,'leave','hr',user_department_ids=['hr'],user_role_ids=['reader'])==[]
    r=client.post(f'/api/documents/{identity}/approve?department_id=hr',headers=headers['admin'],json={
        'expected_version':2,'audiences':[{'department_id':None,'role_id':None}]})
    assert r.status_code==200,r.text
    with Session(central) as s: departments,roles=get_user_context(s,'employee','hr')
    with Session(runtime) as s:
        chunks=rag_service.retrieve_relevant_chunks(s,'إجازة','hr',user_department_ids=departments,user_role_ids=roles)
        assert chunks and all(c['version_number']==2 for c in chunks)
        assert any(corrected in c['content'] for c in chunks)
        assert s.scalar(select(DocumentRevisionLog)).new_text==corrected
        assert sha256(Path(s.get(Document,identity).file_path).read_bytes()).digest()==sha256(original).digest()
        assert rag_service.retrieve_relevant_chunks(s,'إجازة','hr',user_department_ids=['hr'],user_role_ids=[])  # explicit all roles
    # A nearer forbidden chunk must not consume top-K before audience filtering.
    from app.documents.models import DocumentAudience, DocumentChunk, DocumentVersion
    with Session(owner) as s,s.begin():
        forbidden=Document(filename='restricted.pdf',file_path='unused',department_id='hr',status='approved',version=1)
        s.add(forbidden);s.flush()
        s.add(DocumentVersion(document_id=forbidden.id,version_number=1,status='approved'))
        s.add(DocumentAudience(document_id=forbidden.id,department_id=None,role_id='denied-role',created_by='admin'))
        s.add(DocumentChunk(document_id=forbidden.id,version_number=1,content='FORBIDDEN_PRIVATE_EVIDENCE_'+'x'*30,embedding=[1.0]+[0.0]*767,page_number=1))
        for chunk in s.scalars(select(DocumentChunk).where(DocumentChunk.document_id==identity)):
            chunk.embedding=[0.9,0.1]+[0.0]*766
    with Session(runtime) as s:
        ranked=rag_service.retrieve_relevant_chunks(s,'leave','hr',limit=1,user_department_ids=departments,user_role_ids=roles)
        assert len(ranked)==1 and ranked[0]['document_id']==identity
        assert 'FORBIDDEN_PRIVATE_EVIDENCE' not in str(ranked)
    assert client.get(f'/api/documents/download/{identity}?department_id=hr',headers=headers['employee']).status_code==403
    assert client.get(f'/api/documents/{identity}?department_id=finance',headers=headers['employee']).status_code==403
    r=client.post(f'/api/documents/{identity}/archive?department_id=hr',headers=headers['admin'],json={'expected_version':2})
    assert r.status_code==200,r.text
    with Session(runtime) as s:
        assert rag_service.retrieve_relevant_chunks(s,'leave','hr',user_department_ids=departments,user_role_ids=roles)==[]
    runtime.dispose();owner.dispose()
