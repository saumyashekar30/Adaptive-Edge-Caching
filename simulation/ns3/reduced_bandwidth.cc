/*
 * NS-3 Simulation Scenario: Reduced Bandwidth
 * Author: Saumya Shekhar (Modules 5, 6, 8, 9)
 *
 * Throttles link bandwidth to 1 Mbps.
 * Tests edge cache utility offloading and queue saturation under tight bandwidth constraints.
 */

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("AdaptiveEdgeReducedBandwidth");

int main(int argc, char *argv[])
{
    CommandLine cmd(__FILE__);
    double simTime = 60.0;
    cmd.AddValue("simTime", "Simulation time in seconds", simTime);
    cmd.Parse(argc, argv);

    Time::SetResolution(Time::NS);
    LogComponentEnable("AdaptiveEdgeReducedBandwidth", LOG_LEVEL_INFO);

    NodeContainer nodes;
    nodes.Create(6);

    InternetStackHelper internet;
    internet.Install(nodes);

    PointToPointHelper p2pFast;
    p2pFast.SetDeviceAttribute("DataRate", StringValue("100Mbps"));
    p2pFast.SetChannelAttribute("Delay", StringValue("5ms"));

    PointToPointHelper p2pThrottled;
    p2pThrottled.SetDeviceAttribute("DataRate", StringValue("1Mbps")); // 1 Mbps bottleneck
    p2pThrottled.SetChannelAttribute("Delay", StringValue("15ms"));

    NetDeviceContainer d01 = p2pFast.Install(nodes.Get(0), nodes.Get(1));
    NetDeviceContainer d12 = p2pFast.Install(nodes.Get(1), nodes.Get(2));
    NetDeviceContainer d13 = p2pThrottled.Install(nodes.Get(1), nodes.Get(3)); // Throttled Node 2
    NetDeviceContainer d14 = p2pFast.Install(nodes.Get(1), nodes.Get(4));

    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.4.0.0", "255.255.255.0"); ipv4.Assign(d01);
    ipv4.SetBase("10.4.1.0", "255.255.255.0"); ipv4.Assign(d12);
    ipv4.SetBase("10.4.2.0", "255.255.255.0"); ipv4.Assign(d13);
    ipv4.SetBase("10.4.3.0", "255.255.255.0"); ipv4.Assign(d14);

    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    PacketSinkHelper sink("ns3::TcpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), 8080));
    ApplicationContainer sinks = sink.Install(nodes.Get(3));
    sinks.Start(Seconds(0.0));

    OnOffHelper onoff("ns3::TcpSocketFactory", InetSocketAddress(Ipv4Address("10.4.2.2"), 8080));
    onoff.SetAttribute("DataRate", StringValue("10Mbps"));
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
        double mbps = (e.second.rxBytes * 8.0) / (simTime * 1e6);
        std::cout << "[NS-3 Reduced Bandwidth] Throughput: " << mbps << " Mbps\n";
    }

    Simulator::Destroy();
    return 0;
}
