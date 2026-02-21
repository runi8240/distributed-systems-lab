# Generated gRPC protobuf modules are written here by scripts/gen_protos.sh.
#
# grpc_tools generates `import marketplace_pb2` in `marketplace_pb2_grpc.py`.
# When these modules are imported as `common.grpc_gen.*`, expose a compatible
# top-level alias so generated imports resolve in packaged runtimes (e.g. Docker).
import sys

from . import marketplace_pb2 as _marketplace_pb2

sys.modules.setdefault("marketplace_pb2", _marketplace_pb2)
