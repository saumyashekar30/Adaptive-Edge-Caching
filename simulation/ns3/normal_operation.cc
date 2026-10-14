/*
 * NS-3 Simulation Scenario: Normal Operation
 * Author: Saumya Shekhar (Modules 5, 6, 8, 9)
 *
 * Simulates nominal edge network conditions:
 * - 3 Edge Cache Nodes + 1 Traffic Controller + 1 Authoritative Origin
 * - 100 Mbps point-to-point links with 5-10ms nominal propagation delays
 * - Low packet loss (< 0.1%), low queueing delay (< 2ms)
 * - Cache hits serviced at edge with ~1ms latency; misses routed to origin (~60ms)
 */

#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/internet-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/applications-module.h"
#include "ns3/flow-monitor-module.h"

using namespace ns3;

NS_LOG_COMPONENT_DEFINE("AdaptiveEdgeNormalOperation");

int main(int argc, char *argv[])
{
    CommandLine cmd(__FILE__);
    double simTime = 60.0; // seconds
    double requestRate = 120.0; // req/s
    cmd.AddValue("simTime", "Total simulation time in seconds", simTime);
    cmd.AddValue("requestRate", "Request rate in requests per second", requestRate);
    cmd.Parse(argc, argv);

    Time::SetResolution(Time::NS);
    LogComponentEnable("AdaptiveEdgeNormalOperation", LOG_LEVEL_INFO);

    NS_LOG_INFO("Creating Network Topology: Controller -> 3 Edge Nodes -> Origin");

    // Nodes: 0: Client, 1: Controller, 2: EdgeNode1, 3: EdgeNode2, 4: EdgeNode3, 5: Origin
    NodeContainer allNodes;
    allNodes.Create(6);

    Ptr<Node> client     = allNodes.Get(0);
    Ptr<Node> controller = allNodes.Get(1);
    Ptr<Node> edge1      = allNodes.Get(2);
    Ptr<Node> edge2      = allNodes.Get(3);
    Ptr<Node> edge3      = allNodes.Get(4);
    Ptr<Node> origin     = allNodes.Get(5);

    InternetStackHelper internet;
    internet.Install(allNodes);

    // Link Helpers
    PointToPointHelper p2pClientController;
    p2pClientController.SetDeviceAttribute("DataRate", StringValue("1Gbps"));
    p2pClientController.SetChannelAttribute("Delay", StringValue("2ms"));

    PointToPointHelper p2pEdge;
    p2pEdge.SetDeviceAttribute("DataRate", StringValue("100Mbps"));
    p2pEdge.SetChannelAttribute("Delay", StringValue("5ms"));

    PointToPointHelper p2pOrigin;
    p2pOrigin.SetDeviceAttribute("DataRate", StringValue("1Gbps"));
    p2pOrigin.SetChannelAttribute("Delay", StringValue("50ms")); // Origin latency

    // Install NetDevices
    NetDeviceContainer dClientCtrl = p2pClientController.Install(client, controller);
    NetDeviceContainer dCtrlEdge1  = p2pEdge.Install(controller, edge1);
    NetDeviceContainer dCtrlEdge2  = p2pEdge.Install(controller, edge2);
    NetDeviceContainer dCtrlEdge3  = p2pEdge.Install(controller, edge3);
    NetDeviceContainer dEdge1Orig  = p2pOrigin.Install(edge1, origin);
    NetDeviceContainer dEdge2Orig  = p2pOrigin.Install(edge2, origin);
    NetDeviceContainer dEdge3Orig  = p2pOrigin.Install(edge3, origin);

    // Assign IP Addresses
    Ipv4AddressHelper ipv4;

    ipv4.SetBase("10.1.0.0", "255.255.255.0");
    Ipv4InterfaceContainer iClientCtrl = ipv4.Assign(dClientCtrl);

    ipv4.SetBase("10.1.1.0", "255.255.255.0");
    Ipv4InterfaceContainer iCtrlEdge1 = ipv4.Assign(dCtrlEdge1);

    ipv4.SetBase("10.1.2.0", "255.255.255.0");
    Ipv4InterfaceContainer iCtrlEdge2 = ipv4.Assign(dCtrlEdge2);

    ipv4.SetBase("10.1.3.0", "255.255.255.0");
    Ipv4InterfaceContainer iCtrlEdge3 = ipv4.Assign(dCtrlEdge3);

    ipv4.SetBase("10.1.10.0", "255.255.255.0");
    ipv4.Assign(dEdge1Orig);

    ipv4.SetBase("10.1.20.0", "255.255.255.0");
    ipv4.Assign(dEdge2Orig);

    ipv4.SetBase("10.1.30.0", "255.255.255.0");
    ipv4.Assign(dEdge3Orig);

    Ipv4GlobalRoutingHelper::PopulateRoutingTables();

    // Sinks on Edge Nodes (Port 8080)
    uint16_t port = 8080;
    Address sinkAddress(InetSocketAddress(Ipv4Address::GetAny(), port));
    PacketSinkHelper packetSinkHelper("ns3::TcpSocketFactory", sinkAddress);

    ApplicationContainer sinkApps1 = packetSinkHelper.Install(edge1);
    ApplicationContainer sinkApps2 = packetSinkHelper.Install(edge2);
    ApplicationContainer sinkApps3 = packetSinkHelper.Install(edge3);
    sinkApps1.Start(Seconds(0.0));
    sinkApps2.Start(Seconds(0.0));
    sinkApps3.Start(Seconds(0.0));

    // Client Applications (OnOff TCP traffic representing HTTP GET requests)
    OnOffHelper clientHelper("ns3::TcpSocketFactory", Address());
    clientHelper.SetAttribute("OnTime", StringValue("ns3::ConstantRandomVariable[Constant=1]"));
    clientHelper.SetAttribute("OffTime", StringValue("ns3::ExponentialRandomVariable[Mean=0.01]"));
    clientHelper.SetAttribute("DataRate", StringValue("50Mbps"));
    clientHelper.SetAttribute("PacketSize", UintegerValue(1460));

    AddressValue remoteAddress(InetSocketAddress(iCtrlEdge1.GetAddress(1), port));
    clientHelper.SetAttribute("Remote", remoteAddress);

    ApplicationContainer clientApps = clientHelper.Install(client);
    clientApps.Start(Seconds(1.0));
    clientApps.Stop(Seconds(simTime));

    // FlowMonitor for metrics output
    FlowMonitorHelper flowmon;
    Ptr<FlowMonitor> monitor = flowmon.InstallAll();

    NS_LOG_INFO("Running Normal Operation Simulation for " << simTime << " seconds...");
    Simulator::Stop(Seconds(simTime));
    Simulator::Run();

    monitor->CheckForLostPackets();
    Ptr<Ipv4FlowClassifier> classifier = DynamicCast<Ipv4FlowClassifier>(flowmon.GetClassifier());
    std::map<FlowId, FlowMonitor::FlowStats> stats = monitor->GetFlowStats();

    double totalRxBytes = 0;
    double totalDelayMs = 0;
    uint32_t totalPackets = 0;
    uint32_t lostPackets = 0;

    for (auto const &entry : stats)
    {
        totalRxBytes += entry.second.rxBytes;
        lostPackets += entry.second.lostPackets;
        totalPackets += entry.second.rxPackets;
        if (entry.second.rxPackets > 0)
        {
            totalDelayMs += entry.second.delaySum.GetMilliSeconds();
        }
    }

    double avgDelay = totalPackets > 0 ? (totalDelayMs / totalPackets) : 0.0;
    double lossRate = (totalPackets + lostPackets) > 0 ? ((double)lostPackets / (totalPackets + lostPackets) * 100.0) : 0.0;
    double throughputMbps = (totalRxBytes * 8.0) / (simTime * 1e6);

    std::cout << "[NS-3 Normal Operation Results]\n";
    std::cout << "Throughput: " << throughputMbps << " Mbps\n";
    std::cout << "Mean Latency: " << avgDelay << " ms\n";
    std::cout << "Packet Loss: " << lossRate << " %\n";

    Simulator::Destroy();
    return 0;
}
