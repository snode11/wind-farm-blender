"""MPI environment for local solver ranks and independently launched workers."""
import os
import sys


def configure_local_mpi(env=None):
    """Set macOS transport defaults before importing mpi4py; keep caller choices."""
    if env is None:
        env = os.environ
    if sys.platform == 'darwin':
        # Local spawned ranks must share a reachable data interface. External
        # or virtual interfaces can leave matching MPI Send/Recv calls blocked.
        env.setdefault('OMPI_MCA_btl', 'self,tcp')
        if not any(key in env for key in ('OMPI_MCA_btl_tcp_if_include',
                                          'OMPI_MCA_btl_tcp_if_exclude')):
            env['OMPI_MCA_btl_tcp_if_include'] = 'lo0'
    return env


def launcher_environment():
    """Prepare a fresh MPI job without inheriting another launcher's identity."""
    transport_keys = ('OMPI_MCA_btl', 'OMPI_MCA_pml')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(('OMPI_', 'PMI_', 'PMIX_', 'OPAL_', 'PRTE_'))
           or any(key == prefix or key.startswith(prefix + '_')
                  for prefix in transport_keys)}
    return configure_local_mpi(env)
