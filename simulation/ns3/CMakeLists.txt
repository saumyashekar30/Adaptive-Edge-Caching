# CMakeLists.txt for Adaptive Edge Caching NS-3 Simulation Scenarios
cmake_minimum_required(VERSION 3.16)
project(adaptive_edge_caching_simulation)

find_package(ns3 REQUIRED COMPONENTS core network internet point-to-point applications flow-monitor)

set(SCENARIOS
    normal_operation
    high_latency
    packet_loss
    reduced_bandwidth
    congestion
    link_failure
    node_failure
)

foreach(scenario ${SCENARIOS})
    add_executable(${scenario} ${scenario}.cc)
    target_link_libraries(${scenario} PRIVATE
        ${ns3_LIBRARIES}
        ns3::core
        ns3::network
        ns3::internet
        ns3::point-to-point
        ns3::applications
        ns3::flow-monitor
    )
endforeach()
