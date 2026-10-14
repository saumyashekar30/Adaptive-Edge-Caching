/*
 * NS-3 Simulation Scenario: Congestion & Queueing Delay
 * Author: Saumya Shekhar (Modules 5, 6, 8, 9)
 *
 * Configures DropTailQueue with restricted capacity (20 pkts) and injects bursty cross-traffic.
 * Evaluates queue build-up and queueing penalty δ·QueueingDelay.
 */

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("AdaptiveEdgeCongestion");

int main(int argc, char *argv[])
{
    CommandLine cmd(__FILE__);
    double simTime = 60.0;
    cmd.AddValue("simTime", "Simulation time in seconds", simTime);
    cmd.Parse(argc, argv);

    Time::SetResolution(Time::NS);
    LogComponentEnable("AdaptiveEdgeCongestion", LOG_LEVEL_INFO);

    NodeContainer nodes;
    nodes.Create(6);

    InternetStackHelper internet;
    internet.Install(nodes);

    PointToPointHelper p2pCongested;
    p2pCongested.SetDeviceAttribute("DataRate", StringValue("10Mbps"));
    p2pCongested.SetChannelAttribute("Delay", StringValue("10ms"));
    p2pCongested.SetQueue("ns3::DropTailQueue<Packet>", "MaxSize", StringValue("20p")); // Shallow buffer

    PointToPointHelper p2pNormal;
    p2pNormal.SetDeviceAttribute("DataRate", StringValue("100Mbps"));
    p2pNormal.SetChannelAttribute("Delay", StringValue("10ms"));

    NetDeviceContainer d01 = p2pNormal.Install(nodes.Get(0), nodes.Get(1));
    NetDeviceContainer d12 = p2pCongested.Install(nodes.Get(1), nodes.Get(2)); // Congested path to Node 1
    NetDeviceContainer d13 = p2pNormal.Install(nodes.Get(1), nodes.Get(3));
    NetDeviceContainer d14 = p2pNormal.Install(nodes.Get(1), nodes.Get(4));

    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.5.0.0", "255.255.255.0"); ipv4.Assign(d01);
    ipv4.SetBase("10.5.1.0", "255.255.255.0"); ipv4.Assign(d12);
    ipv4.SetBase("10.5.2.0", "255.255.255.0"); ipv4.Assign(d13);
    ipv4.SetBase("10.5.3.0", "255.255.255.0"); ipv4.Assign(d14);

    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    PacketSinkHelper sink("ns3::TcpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), 8080));
    ApplicationContainer sinks = sink.Install(nodes.Get(2));
    sinks.Start(Seconds(0.0));

    // High rate OnOff application inducing buffer overflows
    OnOffHelper burstApp("ns3::TcpSocketFactory", InetSocketAddress(Ipv4Address("10.5.1.2"), 8080));
    burstApp.SetAttribute("DataRate", StringValue("45Mbps"));
    burstApp.SetAttribute("PacketSize", UintegerValue(1460));
    ApplicationContainer apps = burstApp.Install(nodes.Get(0));
    apps.Start(Seconds(1.0));
    apps.Stop(Seconds(simTime));

    FlowMonitorHelper flowmon;
    Ptr<FlowMonitor> monitor = flowmon.InstallAll();

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    monitor->CheckForLostPackets();
    auto stats = monitor->GetFlowStats();
    for (auto const &e : stats) {
        if (e.second.rxPackets > 0) {
            double queueDelayMs = (e.second.delaySum.GetMilliSeconds() / e.second.rxPackets) - 20.0; // Subtract base RTT
            std::cout << "[NS-3 Congestion Trace] Queueing Delay: " << std::max(0.0, queueDelayMs)
                      << " ms, Buffer Drops: " << e.second.lostPackets << " packets\n";
        }
    }

    Simulator::Destroy();
    return 0;
}
