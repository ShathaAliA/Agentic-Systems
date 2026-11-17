"""Offline guard against unsafe defaults reintroduced by concurrent changes."""
import ast
from pathlib import Path
from dotenv import dotenv_values
ROOT=Path(__file__).parents[1]

def test_new_e2e_suite_uses_only_shared_fresh_fixture():
    source=(ROOT/'tests/test_e2e_documents.py').read_text(encoding='utf-8')
    tree=ast.parse(source)
    assert any(isinstance(n,ast.ImportFrom) and n.module=='document_test_infrastructure'
               and any(a.name=='fresh_db' for a in n.names) for n in tree.body)
    assert not any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name=='fresh_db' for n in tree.body)
    assert 'DROP DATABASE' not in source and 'pg_terminate_backend' not in source
    assert 'postgresql://' not in source and 'postgresql+' not in source

def test_example_is_disabled_and_has_no_inline_credentials_or_legacy_passwords():
    values=dotenv_values(ROOT/'.env.example')
    assert values['AUTH_MODE']=='disabled'
    assert values['DATABASE_URL_FILE']=='/run/secrets/api-database.json'
    assert all(not (k.endswith('_URL') or k.endswith('_PASSWORD') or k.endswith('_API_KEY')) for k in values)
    assert all('postgresql' not in (v or '') for v in values.values())
