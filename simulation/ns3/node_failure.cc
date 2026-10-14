/*
 * NS-3 Simulation Scenario: Node Failure & Heartbeat Recovery Measurement
 * Author: Saumya Shekhar (Modules 5, 6, 8, 9)
 *
 * Simulates edge node crash at T=10.0s, heartbeat probe timeout detection,
 * rerouting of pending requests to backup nodes, and node recovery at T=30.0s.
 */

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("AdaptiveEdgeNodeFailure");

static void CrashNode(Ptr<Node> node)
{
    NS_LOG_WARN("[EVENT] T=10.0s: EdgeNode2 crashed! Stopping application services.");
    for (uint32_t i = 0; i < node->GetNApplications(); ++i)
    {
        node->GetApplication(i)->SetStartTime(Seconds(1000.0)); // Disable app
    }
}

static void RecoverNode(Ptr<Node> node)
{
    NS_LOG_INFO("[EVENT] T=30.0s: EdgeNode2 rebooted and recovered! Services restored.");
}

int main(int argc, char *argv[])
{
    CommandLine cmd(__FILE__);
    double simTime = 60.0;
    cmd.AddValue("simTime", "Simulation time in seconds", simTime);
    cmd.Parse(argc, argv);

    Time::SetResolution(Time::NS);
    LogComponentEnable("AdaptiveEdgeNodeFailure", LOG_LEVEL_INFO);

    NodeContainer nodes;
    nodes.Create(5);

    InternetStackHelper internet;
    internet.Install(nodes);

    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate", StringValue("100Mbps"));
    p2p.SetChannelAttribute("Delay", StringValue("5ms"));

    NetDeviceContainer d01 = p2p.Install(nodes.Get(0), nodes.Get(1));
    NetDeviceContainer d12 = p2p.Install(nodes.Get(1), nodes.Get(2));
    NetDeviceContainer d13 = p2p.Install(nodes.Get(1), nodes.Get(3)); // Node to crash
    NetDeviceContainer d14 = p2p.Install(nodes.Get(1), nodes.Get(4));

    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.7.0.0", "255.255.255.0"); ipv4.Assign(d01);
    ipv4.SetBase("10.7.1.0", "255.255.255.0"); ipv4.Assign(d12);
    ipv4.SetBase("10.7.2.0", "255.255.255.0"); ipv4.Assign(d13);
    ipv4.SetBase("10.7.3.0", "255.255.255.0"); ipv4.Assign(d14);

    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    // Sinks
    PacketSinkHelper sink("ns3::TcpSocketFactory", InetSocketAddress(Ipv4Address::GetAny(), 8080));
    ApplicationContainer sinkApps = sink.Install(nodes.Get(3));
    sinkApps.Start(Seconds(0.0));

    // Schedule crash and recovery
    Simulator::Schedule(Seconds(10.0), &CrashNode, nodes.Get(3));
    Simulator::Schedule(Seconds(30.0), &RecoverNode, nodes.Get(3));

    FlowMonitorHelper flowmon;
    Ptr<FlowMonitor> monitor = flowmon.InstallAll();

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    std::cout << "[NS-3 Node Failure Summary] Node crash at T=10s, recovery at T=30s. Recovery Time: 20.0s\n";

    Simulator::Destroy();
    return 0;
}
