/*
 * NS-3 Simulation Scenario: High Latency
 * Author: Saumya Shekhar (Modules 5, 6, 8, 9)
 *
 * Injects 180ms propagation delay on link to Node 3.
 * Evaluates impact of latency score α·L in shifting routing to lower latency nodes (Node 1/2).
 */

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("AdaptiveEdgeHighLatency");

int main(int argc, char *argv[])
{
    CommandLine cmd(__FILE__);
    double simTime = 60.0;
    cmd.AddValue("simTime", "Simulation time in seconds", simTime);
    cmd.Parse(argc, argv);

    Time::SetResolution(Time::NS);
    LogComponentEnable("AdaptiveEdgeHighLatency", LOG_LEVEL_INFO);

    NodeContainer nodes;
    nodes.Create(6); // 0: Client, 1: Ctrl, 2: Edge1, 3: Edge2, 4: Edge3 (WAN), 5: Origin

    InternetStackHelper internet;
    internet.Install(nodes);

    PointToPointHelper p2pNormal;
    p2pNormal.SetDeviceAttribute("DataRate", StringValue("100Mbps"));
    p2pNormal.SetChannelAttribute("Delay", StringValue("8ms"));

    PointToPointHelper p2pHighLatency;
    p2pHighLatency.SetDeviceAttribute("DataRate", StringValue("100Mbps"));
    p2pHighLatency.SetChannelAttribute("Delay", StringValue("180ms")); // WAN bottleneck

    NetDeviceContainer d01 = p2pNormal.Install(nodes.Get(0), nodes.Get(1));
    NetDeviceContainer d12 = p2pNormal.Install(nodes.Get(1), nodes.Get(2));
    NetDeviceContainer d13 = p2pNormal.Install(nodes.Get(1), nodes.Get(3));
    NetDeviceContainer d14 = p2pHighLatency.Install(nodes.Get(1), nodes.Get(4)); // High latency node
    NetDeviceContainer dOrigin = p2pNormal.Install(nodes.Get(1), nodes.Get(5));

    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.2.0.0", "255.255.255.0"); ipv4.Assign(d01);
    ipv4.SetBase("10.2.1.0", "255.255.255.0"); ipv4.Assign(d12);
    ipv4.SetBase("10.2.2.0", "255.255.255.0"); ipv4.Assign(d13);
    ipv4.SetBase("10.2.3.0", "255.255.255.0"); ipv4.Assign(d14);
    ipv4.SetBase("10.2.10.0", "255.255.255.0"); ipv4.Assign(dOrigin);

    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    PacketSinkHelper sink("ns3::TcpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), 8080));
    ApplicationContainer sinks = sink.Install(nodes.Get(4)); // WAN node
    sinks.Start(Seconds(0.0));

    OnOffHelper onoff("ns3::TcpSocketFactory", InetSocketAddress(Ipv4Address("10.2.3.2"), 8080));
    onoff.SetAttribute("DataRate", StringValue("20Mbps"));
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
        if (e.second.rxPackets > 0) {
            std::cout << "[NS-3 High Latency Trace] Flow " << e.first
                      << " Delay: " << (e.second.delaySum.GetMilliSeconds() / e.second.rxPackets) << " ms\n";
        }
    }

    Simulator::Destroy();
    return 0;
}
