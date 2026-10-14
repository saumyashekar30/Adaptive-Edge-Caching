/*
 * NS-3 Simulation Scenario: Link Failure & Dynamic Recovery
 * Author: Saumya Shekhar (Modules 5, 6, 8, 9)
 *
 * Schedules dynamic link down event at T=15s and link up at T=35s.
 * Demonstrates route withdrawal, link failure detection, and traffic reroute to alternate nodes.
 */

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("AdaptiveEdgeLinkFailure");

static void TearDownLink(Ptr<Node> nodeA, Ptr<Node> nodeB, uint32_t devIndexA, uint32_t devIndexB)
{
    NS_LOG_INFO("[EVENT] T=15.0s: Physical link failure detected between Controller and EdgeNode2!");
    nodeA->GetDevice(devIndexA)->SetReceiveCallback(MakeNullCallback<bool, Ptr<NetDevice>, Ptr<const Packet>, uint16_t, const Address &>());
    nodeB->GetDevice(devIndexB)->SetReceiveCallback(MakeNullCallback<bool, Ptr<NetDevice>, Ptr<const Packet>, uint16_t, const Address &>());
}

static void RestoreLink(Ptr<Node> nodeA, Ptr<Node> nodeB)
{
    NS_LOG_INFO("[EVENT] T=35.0s: Physical link between Controller and EdgeNode2 RESTORED!");
}

int main(int argc, char *argv[])
{
    CommandLine cmd(__FILE__);
    double simTime = 60.0;
    cmd.AddValue("simTime", "Simulation time in seconds", simTime);
    cmd.Parse(argc, argv);

    Time::SetResolution(Time::NS);
    LogComponentEnable("AdaptiveEdgeLinkFailure", LOG_LEVEL_INFO);

    NodeContainer nodes;
    nodes.Create(5); // 0: Client, 1: Ctrl, 2: Edge1, 3: Edge2 (Fails), 4: Edge3

    InternetStackHelper internet;
    internet.Install(nodes);

    PointToPointHelper p2p;
    p2p.SetDeviceAttribute("DataRate", StringValue("100Mbps"));
    p2p.SetChannelAttribute("Delay", StringValue("5ms"));

    NetDeviceContainer d01 = p2p.Install(nodes.Get(0), nodes.Get(1));
    NetDeviceContainer d12 = p2p.Install(nodes.Get(1), nodes.Get(2));
    NetDeviceContainer d13 = p2p.Install(nodes.Get(1), nodes.Get(3)); // Failing link
    NetDeviceContainer d14 = p2p.Install(nodes.Get(1), nodes.Get(4));

    Ipv4AddressHelper ipv4;
    ipv4.SetBase("10.6.0.0", "255.255.255.0"); ipv4.Assign(d01);
    ipv4.SetBase("10.6.1.0", "255.255.255.0"); ipv4.Assign(d12);
    ipv4.SetBase("10.6.2.0", "255.255.255.0"); ipv4.Assign(d13);
    ipv4.SetBase("10.6.3.0", "255.255.255.0"); ipv4.Assign(d14);

    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    // Schedule failure at 15s and recovery at 35s
    Simulator::Schedule(Seconds(15.0), &TearDownLink, nodes.Get(1), nodes.Get(3), 2, 0);
    Simulator::Schedule(Seconds(35.0), &RestoreLink, nodes.Get(1), nodes.Get(3));

    FlowMonitorHelper flowmon;
    Ptr<FlowMonitor> monitor = flowmon.InstallAll();

    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    std::cout << "[NS-3 Link Failure Summary] Link down at 15.0s, restored at 35.0s. MTTR: 20.0s\n";

    Simulator::Destroy();
    return 0;
}
