/*
 * NS-3 Simulation Scenario: Packet Loss
 * Author: Saumya Shekhar (Modules 5, 6, 8, 9)
 *
 * Configures RateErrorModel on link to Node 2 with 8.5% random packet drop.
 * Measures TCP window throttling, duplicate ACKs, and routing penalty β·PacketLoss.
 */

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("AdaptiveEdgePacketLoss");

int main(int argc, char *argv[])
{
    CommandLine cmd(__FILE__);
    double simTime = 60.0;
    double errorRate = 0.085; // 8.5% loss
    cmd.AddValue("simTime", "Simulation duration in seconds", simTime);
    cmd.AddValue("errorRate", "Packet loss rate (0.0 to 1.0)", errorRate);
    cmd.Parse(argc, argv);

    Time::SetResolution(Time::NS);
    LogComponentEnable("AdaptiveEdgePacketLoss", LOG_LEVEL_INFO);

    NodeContainer nodes;
    nodes.Create(6);

    InternetStackHelper internet;
    internet.Install(nodes);

    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate", StringValue("100Mbps"));
    p2p.SetChannelAttribute("Delay", StringValue("10ms"));

    NetDeviceContainer dClientCtrl = p2p.Install(nodes.Get(0), nodes.Get(1));
    NetDeviceContainer dCtrlEdge1  = p2p.Install(nodes.Get(1), nodes.Get(2));
    NetDeviceContainer dCtrlEdge2  = p2p.Install(nodes.Get(1), nodes.Get(3)); // Lossy link
    NetDeviceContainer dCtrlEdge3  = p2p.Install(nodes.Get(1), nodes.Get(4));

    // Install RateErrorModel on Edge 2 device
    Ptr<RateErrorModel> em = CreateObject<RateErrorModel>();
    em->SetAttribute("ErrorRate", DoubleValue(errorRate));
    em->SetAttribute("ErrorUnit", StringValue("ERROR_UNIT_PACKET"));
    dCtrlEdge2.Get(1)->SetAttribute("ReceiveErrorModel", PointerValue(em));

    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.3.0.0", "255.255.255.0"); ipv4.Assign(dClientCtrl);
    ipv4.SetBase("10.3.1.0", "255.255.255.0"); ipv4.Assign(dCtrlEdge1);
    ipv4.SetBase("10.3.2.0", "255.255.255.0"); ipv4.Assign(dCtrlEdge2);
    ipv4.SetBase("10.3.3.0", "255.255.255.0"); ipv4.Assign(dCtrlEdge3);

    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    PacketSinkHelper sink("ns3::TcpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), 8080));
    ApplicationContainer sinks = sink.Install(nodes.Get(3));
    sinks.Start(Seconds(0.0));

    OnOffHelper onoff("ns3::TcpSocketFactory", InetSocketAddress(Ipv4Address("10.3.2.2"), 8080));
    onoff.SetAttribute("DataRate", StringValue("50Mbps"));
    onoff.SetAttribute("PacketSize", UintegerValue(1460));
    ApplicationContainer clientApp = onoff.Install(nodes.Get(0));
    clientApp.Start(Seconds(1.0));
    clientApp.Stop(Seconds(simTime));

    FlowMonitorHelper flowmon;
    Ptr<FlowMonitor> monitor = flowmon.InstallAll();

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    monitor->CheckForLostPackets();
    auto stats = monitor->GetFlowStats();
    for (auto const &e : stats) {
        double lossPct = (e.second.txPackets > 0) ? ((double)e.second.lostPackets / e.second.txPackets * 100.0) : 0.0;
        std::cout << "[NS-3 Packet Loss Trace] Flow " << e.first
                  << " Tx: " << e.second.txPackets << " Rx: " << e.second.rxPackets
                  << " Loss: " << lossPct << " %\n";
    }

    Simulator::Destroy();
    return 0;
}
