package black_box_cbs.generated;

import inet.queueing.sink.PassivePacketSink;

simple EthernetFlowSink extends PassivePacketSink
{
    parameters:
        @class(inet::queueing::PassivePacketSink);
        @statistic[sequenceNumber](source=appPkSeqNo(packetPushed); record=vector; interpolationmode=none);
}
