package black_box_cbs.generated;

import inet.queueing.contract.IPacketFlow;
import inet.queueing.filter.ContentBasedFilter;
import inet.linklayer.ieee8021q.Ieee8021qTagEpdHeaderChecker;

// Ethernet sockets receive tagged frames after MAC decapsulation. Select the
// named flow, then remove/validate its C-tag before delivering the payload.
module FlowReceiver like IPacketFlow
{
    gates:
        input in;
        output out;
    submodules:
        filter: ContentBasedFilter;
        vlan: Ieee8021qTagEpdHeaderChecker;
    connections:
        in --> filter.in;
        filter.out --> vlan.in;
        vlan.out --> out;
}
