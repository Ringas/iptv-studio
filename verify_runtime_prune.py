"""裁剪必须保留间接依赖、动态加载模块、证书/时区和许可；异常时不删除。"""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('runtime_prune',Path(__file__).with_name('runtime-prune.py'))
pruner=importlib.util.module_from_spec(spec);spec.loader.exec_module(pruner)


class RuntimePruneTests(unittest.TestCase):
    def fixture(self, root):
        deps={'node':{'libstdc++.so.6'},'ffmpeg':set(),'python3.13':{'libpython3.13.so.1.0'},
              'libpython3.13.so.1.0':{'libc.so.6'},'libstdc++.so.6':{'libgcc_s.so.1'},
              'libgcc_s.so.1':{'libc.so.6'},'libc.so.6':set(),'libssl.so.3':{'libcrypto.so.3'},
              'libcrypto.so.3':{'libc.so.6'},'_ssl.so':{'libssl.so.3'},'legacy.so':{'libcrypto.so.3'},
              'libnss_files.so.2':{'libc.so.6'},'libunused.so.1':set()}
        files={**{f'usr/local/bin/{n}':b'elf' for n in ('node','ffmpeg','python3.13')},
               **{f'usr/lib/x86_64-linux-gnu/{n}':b'elf' for n in deps if n.startswith('lib') and not n.startswith('libpython')},
               'usr/local/lib/libpython3.13.so.1.0':b'elf',
               'usr/local/lib/python3.13/lib-dynload/_ssl.so':b'elf',
               'usr/lib/x86_64-linux-gnu/ossl-modules/legacy.so':b'elf',
               'usr/lib/apt/methods/http':b'not used',
               'usr/local/lib/python3.13/lib-dynload/_testcapi.so':b'self-test',
               'usr/share/doc/pkg/copyright':b'license','usr/share/doc/pkg/LICENSE':b'license',
               'usr/share/doc/pkg/changelog.gz':b'build documentation',
               'usr/local/share/doc/ffmpeg/README.md':b'license references',
               'etc/ssl/certs/ca-certificates.crt':b'CA',
               'usr/share/zoneinfo/Asia/Shanghai':b'timezone'}
        for name,data in files.items():
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
        (root/'usr/lib/x86_64-linux-gnu/libunused.so').symlink_to('libunused.so.1')
        return lambda p:(deps[p.name],None) if p.name in deps else None

    def test_dependency_closure_and_retained_data(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);reader=self.fixture(root)
            with patch.object(pruner,'elf_needed',side_effect=reader),contextlib.redirect_stdout(io.StringIO()):
                result=pruner.prune(root)
            self.assertFalse((root/'usr/lib/x86_64-linux-gnu/libunused.so.1').exists())
            self.assertFalse((root/'usr/lib/x86_64-linux-gnu/libunused.so').is_symlink())
            self.assertFalse((root/'usr/lib/apt').exists())
            self.assertFalse((root/'usr/local/lib/python3.13/lib-dynload/_testcapi.so').exists())
            for name in ('libgcc_s.so.1','libcrypto.so.3','libnss_files.so.2'):
                self.assertTrue((root/'usr/lib/x86_64-linux-gnu'/name).exists())
            for name in ('usr/lib/x86_64-linux-gnu/ossl-modules/legacy.so','usr/share/doc/pkg/copyright','usr/share/doc/pkg/LICENSE','usr/local/share/doc/ffmpeg/README.md','etc/ssl/certs/ca-certificates.crt','usr/share/zoneinfo/Asia/Shanghai'):
                self.assertTrue((root/name).exists(),name)
            self.assertGreater(result['removed_bytes'],0)
            with patch.object(pruner,'elf_needed',side_effect=reader),contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(pruner.prune(root)['removed_bytes'],0)
            self.assertFalse((root/'usr/share/doc/pkg/changelog.gz').exists())

    def test_missing_dependency_does_not_delete(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);reader=self.fixture(root)
            def broken(p):return ({'libmissing.so'},None) if p.name=='node' else reader(p)
            with patch.object(pruner,'elf_needed',side_effect=broken):
                with self.assertRaisesRegex(ValueError,'缺少运行依赖'):pruner.prune(root)
            self.assertTrue((root/'usr/lib/x86_64-linux-gnu/libunused.so.1').exists())
            self.assertTrue((root/'usr/lib/apt/methods/http').exists())
            self.assertTrue((root/'usr/share/doc/pkg/changelog.gz').exists())

    def test_absolute_and_escaping_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);self.fixture(root)
            (root/'lib').symlink_to('/usr/lib')
            self.assertEqual(pruner.resolve(root,root/'lib/x86_64-linux-gnu/libc.so.6'),root/'usr/lib/x86_64-linux-gnu/libc.so.6')
            (root/'escape').symlink_to('../outside')
            with self.assertRaisesRegex(ValueError,'越界'):pruner.resolve(root,root/'escape')
        with self.assertRaises(ValueError):pruner.prune(Path('/'))

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux 构建验证中读取真实 ELF')
    def test_native_elf_reader(self):
        deps,interpreter=pruner.elf_needed(Path(sys.executable).resolve())
        self.assertIn('libc.so.6',deps)
        self.assertTrue(interpreter.startswith('/'))


if __name__=='__main__':unittest.main()
