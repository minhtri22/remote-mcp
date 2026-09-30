from __future__ import annotations

import pytest
from pathlib import Path

from remotemcp.node.cli import _read_delete_code,parser
from remotemcp.durable.errors import DurableError


def test_pair_code_file_is_one_line_trimmed_and_deleted(tmp_path):
    p=tmp_path/"code.txt";p.write_text("pair_x|pc1_secret\r\n",encoding="utf-8")
    assert _read_delete_code(p)=="pair_x|pc1_secret"
    assert not p.exists()
    args=parser().parse_args(["pair","--url","https://example.com","--code-file","x","--name","n","--root","r","--runtime-dir","rt"])
    assert args.command=="pair"


def test_pair_code_delete_failure_is_fatal_before_network():
    class Fake:
        def read_text(self,encoding=None):return "pair|code\n"
        def unlink(self):raise OSError("locked")
    with pytest.raises(DurableError) as exc:_read_delete_code(Fake())
    assert exc.value.code=="PAIRING_CODE_FILE_DELETE_FAILED"
