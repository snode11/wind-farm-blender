"""Local transport defaults must not erase the identity of an active MPI rank."""
import pytest

from wfrl import mpi_runtime


@pytest.mark.parametrize('platform', ['darwin', 'linux', 'win32'])
def test_configuring_current_rank_preserves_job_identity(monkeypatch, platform):
    env = {'OMPI_COMM_WORLD_SIZE': '1', 'PMIX_NAMESPACE': 'current-job', 'PATH': '/bin'}
    original = env.copy()
    monkeypatch.setattr(mpi_runtime.sys, 'platform', platform)
    monkeypatch.setattr(mpi_runtime.os, 'environ', env)

    assert mpi_runtime.configure_local_mpi() is env
    expected = original.copy()
    if platform == 'darwin':
        expected.update(OMPI_MCA_btl='self,tcp', OMPI_MCA_btl_tcp_if_include='lo0')
    assert env == expected
    mpi_runtime.configure_local_mpi()
    assert env == expected


def test_macos_current_rank_keeps_explicit_interface_exclusion(monkeypatch):
    env = {'OMPI_COMM_WORLD_SIZE': '1', 'OMPI_MCA_btl': 'self,tcp',
           'OMPI_MCA_btl_tcp_if_exclude': 'lo0', 'OMPI_MCA_pml': 'ob1'}
    original = env.copy()
    monkeypatch.setattr(mpi_runtime.sys, 'platform', 'darwin')

    mpi_runtime.configure_local_mpi(env)

    assert env == original
