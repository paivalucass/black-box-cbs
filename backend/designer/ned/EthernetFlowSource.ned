package black_box_cbs.generated;

import inet.queueing.source.ActivePacketSource;

// Sequence observations distinguish packets emitted at the same burst timestamp.
simple EthernetFlowSource extends ActivePacketSource
{
    parameters:
        @class(inet::queueing::ActivePacketSource);
        packetRepresentation = default("applicationPacket");
        @statistic[sequenceNumber](source=appPkSeqNo(packetPushed); record=vector; interpolationmode=none);
}
