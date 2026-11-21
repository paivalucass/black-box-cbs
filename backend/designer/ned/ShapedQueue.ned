package black_box_cbs.generated;

import inet.queueing.queue.CompoundPacketQueueBase;
import inet.queueing.contract.IPacketGate;
import inet.queueing.queue.PacketQueue;

// IPacketQueue-compatible FIFO + optional CBS gate for INET PriorityQueue.
module ShapedQueue extends CompoundPacketQueueBase
{
    parameters:
        @class(inet::queueing::CompoundPacketQueueBase);
        string bufferModule = default("");
        double bitrate @unit(bps);
    submodules:
        fifo: PacketQueue {
            bufferModule = default(parent.bufferModule);
        }
        shaper: <default("PeriodicGate")> like IPacketGate {
            bitrate = default(parent.bitrate);
            extraLength = default(64b);
        }
    connections:
        in --> fifo.in;
        fifo.out --> shaper.in;
        shaper.out --> out;
}
