"""Rendering-script contracts only; mocked subprocess never opens Word."""
import base64
from pathlib import Path
import re
from types import SimpleNamespace
import zipfile

import pytest

from app import documents


@pytest.fixture
def captured_render(tmp_path,monkeypatch):
    source=tmp_path/'original.docx';source.write_bytes(b'original source remains untouched')
    target=tmp_path/'new.pdf';updated=tmp_path/'new-updated.docx'
    powershell=tmp_path/'Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
    powershell.parent.mkdir(parents=True);powershell.touch()
    monkeypatch.setenv('SystemRoot',str(tmp_path/'Windows'))
    monkeypatch.setattr(documents.os,'name','nt');monkeypatch.setattr(documents,'Path',type(tmp_path))
    calls=[]
    def run(command,**kwargs):
        script=base64.b64decode(command[-1]).decode('utf-16le');calls.append(script)
        Path(kwargs['env']['BIDDING_RENDER_OUTPUT']).write_bytes(b'%PDF-'+b'x'*200)
        with zipfile.ZipFile(kwargs['env']['BIDDING_RENDER_UPDATED_DOCX'],'w') as archive:
            archive.writestr('word/document.xml','<document>fake updated field output</document>')
        return SimpleNamespace(returncode=0,stdout=b'BIDDING_RENDER_OK',stderr=b'')
    monkeypatch.setattr(documents.subprocess,'run',run)
    result=documents.render_pdf(str(source),str(target),str(updated))
    assert len(calls)==1 and result['path']==str(target) and result['updated_docx_path']==str(updated)
    assert source.read_bytes()==b'original source remains untouched'
    return calls[0]


def test_snapshots_business_fields_before_expansion_and_keeps_final_pagination(captured_render):
    s=captured_render
    steps=['$word.Documents.Open','foreach ($field in $document.Fields)',
           '$document.Fields.Update()','$toc.Update()','$document.Repaginate()',
           '$toc.UpdatePageNumbers()','[void]$businessField.Update()',
           '$document.SaveAs2','$document.ExportAsFixedFormat']
    positions=[s.index(step) for step in steps]
    assert positions==sorted(positions)
    assert s.count('foreach ($field in $document.Fields)')==1
    final=s[s.index('$toc.UpdatePageNumbers()'):s.index('$document.SaveAs2')]
    assert '$document.Fields' not in final
    assert 'foreach ($businessField in $businessPageFields)' in final
    assert 'FinalReleaseComObject($businessField)' in s and '$businessPageFields.Clear()' in s
    assert '$word.AutomationSecurity = 3' in s and '$word.Options.UpdateLinksAtOpen = $false' in s


@pytest.mark.parametrize(('code','expected'),[
    (' PAGEREF mxs_'+'a'*32+' \\h ',True),
    ('PAGEREF "mxs_'+'B'*32+'" \\h',True),
    ('PAGEREF _Toc123456789 \\h',False),
    ('PAGEREF "_Toc123456789" \\h',False),
    ('PAGEREF another_bookmark',False),
    ('PAGEREF mxs_'+'a'*31,False),
    ('PAGEREF mxs_'+'a'*32+'_suffix \\h',False),
    ('REF mxs_'+'a'*32,False),
    ('PAGE',False),
])
def test_field_filter_is_only_the_exact_business_bookmark_namespace(captured_render,code,expected):
    pattern=re.search(r"\$field.Code.Text -match '([^']+)'",captured_render)[1]
    assert bool(re.match(pattern,code,re.I)) is expected


def test_expanded_toc_does_not_expand_the_fixed_business_field_worklist(captured_render):
    pattern=re.search(r"\$field.Code.Text -match '([^']+)'",captured_render)[1]
    original=['TOC \\o "1-3"']+[f'PAGEREF mxs_{i:032x} \\h' for i in range(12)]
    captured=[value for value in original if re.match(pattern,value,re.I)]
    expanded=original+[f'PAGEREF _Toc{i} \\h' for i in range(383)]
    assert len(captured)==12
    assert captured==[value for value in expanded if re.match(pattern,value,re.I)]
    assert all('_Toc' not in value for value in captured)
