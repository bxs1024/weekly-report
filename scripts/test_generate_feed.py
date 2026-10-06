import runpy
import xml.etree.ElementTree as ET
from pathlib import Path


def test_generated_feed_is_valid_atom():
    root_dir = Path(__file__).resolve().parents[1]
    feed_path = root_dir / 'docs' / 'feed.xml'
    # generate_feed.py 的产出路径固定为 docs/feed.xml，跑它必然覆盖仓库文件。
    # 测试不该改仓库内容，因此先备份、跑完恢复（含原文件不存在的情形）。
    original = feed_path.read_bytes() if feed_path.exists() else None
    try:
        runpy.run_path(str(root_dir / 'generate_feed.py'))
        root = ET.fromstring(feed_path.read_text(encoding='utf-8'))
        assert root.tag == '{http://www.w3.org/2005/Atom}feed'
        entries = root.findall('{http://www.w3.org/2005/Atom}entry')
        assert entries
        alternate = root.find("{http://www.w3.org/2005/Atom}link[@rel='alternate']")
        assert alternate is not None
        assert alternate.get('href') == 'https://bxs1024.github.io/weekly-report/'
        for entry in entries:
            link = entry.find('{http://www.w3.org/2005/Atom}link')
            assert link is not None
            assert link.get('href')
            assert entry.find('{http://www.w3.org/2005/Atom}published') is not None
    finally:
        if original is None:
            feed_path.unlink(missing_ok=True)
        else:
            feed_path.write_bytes(original)


if __name__ == '__main__':
    test_generated_feed_is_valid_atom()
    print('generate feed tests passed')
