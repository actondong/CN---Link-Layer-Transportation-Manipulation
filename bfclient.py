#!/usr/bin/python
# -*- coding: utf-8 -*-
__author__ = 'Acton'
import socket
import sys
import os
import time
import threading
import select
import Queue
import collections
import copy
import collections
CHUNKSIZE = 20480
BUFF = 40960
TIMEOUT = None
TIMEOUT_ACK_GIVEUP = 10#I fix this value, I don't want keep resending for more than 10 seconds.
TIMEOUT_ACK_RESEND = 2#if not ack, resend every 2 seconds
ACK_NUM = 0
HOST = None
PORT = None
OUTPOOL_LOCK = threading.Lock()
OUTPOOL = []
PROXY = False
PROXY_IP = None
PROXY_PORT = None
READY_TO_WRITE = False#control when to start writing out a received file

BIG_CONTENT_DICT_LOCK = threading.Lock()
#key = fileName value = [(seq,content)]
BIG_CONTENT_DICT = collections.defaultdict(list)

#TODO The general architecture
NEIGHBORS_ORIGIN = {
    #Once initialized, this table will never change.
}

NEIGHBORS_INFO = {
  #This is a dynamic version of NEIGNBORS_ORIGIN. For each entry in NEIGHBORS_INFO, key is (IP,Port) tuple and value is the link weight.
  #This table is initialized based on config file given to start
  #the program and will be updated when neighbors disconnect or new neighbors connect
}

NEIGHBORS_PREVIOUS = {
    #This hashtable is used for functionality --LINKDOWN and LINKUP
    #This may or may not be necessary but till now this is the implementation I came up with

    #This works as a copy of NEIGHBORS_INFO, except when NEIGHBOS_INFO is changed to Infinity, this will keep the link cost before the change.
}


NEIGHBORS_TIMER_INFO_LOCK = threading.Lock()
NEIGHBORS_TIMER_INFO = {
 #I will maintain A timer for each neighbor.
 # Whenever ROUTE UPDATE message is received from a neighbor, that neighbor's timestamp is reset.
 #If 3*timeout has passed but without receiving ROUTE UPDATE message from a neighbor,
 # that neighbor will be decided disconnected.
}

DV_INFO_LOCK = threading.Lock()
DV_INFO = {
 #key is (ip, port), value is weight
 #This is the Distance Vector info table, we record shortest 'distance' to each node in the topology for ???each node??? known in the topology
 #This will be updated periodically based on ROUTE UPDATE message or timeout
}

DV_NXT_HOP = {
 # key is (ip,port), value is (ip,port)
 #This is a optimization. Always record next hop to reach each clnt thru shortest parth
}


DATA_WAITING_FOR_ACK_LOCK = threading.Lock()
DATA_WAITING_FOR_ACK = {
 # key is ACK number, value is (data_sent,time_when_sent)
 # Every msg sent out will be recorded in this dict. When ack is received, record will be deleted
}

PROXY_NEIGHBORS_LOCK = threading.Lock()
PROXY_NEIGHBORS = {
    #neighors for which we must go through proxies to reach.
}
def pack(ID, hashtable):  # wrap ROUTE UPDATE message into string
    ID_str = ID[0] + ":" + ID[1] #(HOST,PORT） --> HOST:PORT
    s = "#" + ID_str + "="
    for key in hashtable:
        key_str = key[0] + ":"+key[1]
        s = s + key_str + "-" + str(hashtable[key]) + ">"
    s.strip()
    return s


def unpack(data):  # unpack the ROUTE UPDATE message
    first_lvl_data_list = data.split("=")
    ID_str = first_lvl_data_list[0][1:]
    ID = tuple(ID_str.split(":"))
    hashtable = {}
    key_value_list = first_lvl_data_list[1].split(">")
    #print key_value_list
    for key_value in key_value_list:
        #print key_value
        if key_value == "":
            continue
        key_value_l = key_value.split("-")
        key_value_l[0] = tuple(key_value_l[0].split(":"))#HOST:PORT ---> (HOST,PORT)
        if key_value_l[1] == "Infinity":
            hashtable[key_value_l[0]] = key_value_l[1]
        else:
            hashtable[key_value_l[0]] = float(key_value_l[1])

    #print pack(ID,hashtable)
    return (ID, hashtable)

def poison_reverse(neighbor,dv_to_send):
    global HOST
    global PORT
    global BUFF
    global DV_INFO
    global DV_NXT_HOP
    global DV_INFO_LOCK
    DV_INFO_LOCK.acquire()
    ID , hashtable = unpack(dv_to_send)
    for dest in DV_NXT_HOP.keys():
        if dest != neighbor and DV_NXT_HOP[dest] == neighbor:
            hashtable[dest] = 'Infinity'
    poison_dv_to_send = pack(ID,hashtable)
    DV_INFO_LOCK.release()
    return poison_dv_to_send


def show_chart():
    global DV_INFO
    global DV_NXT_HOP
    global DV_INFO_LOCK
    DV_INFO_LOCK.acquire()
    for dest in DV_INFO:
        hop = DV_NXT_HOP[dest]
        print "\nDESTINATION = "+str(dest)+" Cost = "+ str(DV_INFO[dest]) + " Link = "+str(hop)
    DV_INFO_LOCK.release()

#This is the function to call when CLOSE.
#What it does is to inform neighbors that I am closing. This design is for sake of stabilization.
#If I don't organize informing neighbors when closing, instead using ctrl+c and wait for 3*TIMEOUT may cause weird behavior
#among neighbors sometime, which is not predictable.
def last_words():
    global NEIGHBORS_INFO
    global OUTPOOL_LOCK
    global OUTPOOL
    OUTPOOL_LOCK.acquire()
    #The below part is added unintentionally to eliminate some unstable behavior after CLOSE for 3*TIMEOUT in origin design
    for dest in NEIGHBORS_INFO:
        if NEIGHBORS_INFO[dest] != "Infinity":
            command_info = "$"+"LINKDOWN"+" "+dest[0]+":"+dest[1]
            OUTPOOL.append(command_info)
    command_info = "$"+"CLOSE"+" "+HOST+":"+PORT
    OUTPOOL.append(command_info)
    OUTPOOL_LOCK.release()
    time.sleep(2)

#This is a yield function helper for large data reading and processing
def read_in_chunks(file_name, chunk_size=CHUNKSIZE):
    while True:
        data = file_name.read(chunk_size)
        if not data:
            break
        yield data

#This function inludes framentation a big file, package it with header, put separated chunks into OUTPOOL.
#Caution, I don't put and send, I put all and send all. Which means, don't try to put in 8G files ...
def transfer_big_file(file_name,dest):
    global DV_NXT_HOP
    global DV_INFO_LOCK
    global OUTPOOL
    global OUTPOOL_LOCK
    global DATA_WAITING_FOR_ACK
    global DATA_WAITING_FOR_ACK_LOCKdata_list
    f = open(file_name,'r')
    DV_INFO_LOCK.acquire()
    OUTPOOL_LOCK.acquire()
    seq = 0
    DATA_WAITING_FOR_ACK_LOCK.acquire()
    for chunck in read_in_chunks(f):
        next_hop = DV_NXT_HOP[dest]
        header = "@"+file_name+" "+dest[0]+":"+dest[1]+" "+next_hop[0]+":"+next_hop[1]+" "+HOST+":"+PORT +" "+ str(seq)
        #==========checksum====================
        content_checksum = sum(bytearray(chunck))
        print "CHECKSUM = " + str(content_checksum)
        chunck += " "+str(content_checksum)
        #======================================
        package_to_send = header +" "+ chunck
        #==========ack ========================
        package_to_send = ack_increment(package_to_send)
        #======================================
        OUTPOOL.append(package_to_send)
        seq += 1

    DATA_WAITING_FOR_ACK_LOCK.release()
    OUTPOOL_LOCK.release()
    DV_INFO_LOCK.release()

#This function pass on file chunks to destination, if already arrive at destination, report it and done.
def relay_race(file_package_data):
    global DV_NXT_HOP
    global DV_INFO_LOCK
    global OUTPOOL
    global OUTPOOL_LOCK
    global  HOST
    global  PORT
    global BIG_CONTENT_DICT
    global READY_TO_WRITE
    data_list = file_package_data[1:].split(" ")
    file_name = data_list[0]
    dest = tuple(data_list[1].split(":"))
    source = tuple(data_list[3].split(":"))
    seq = data_list[4]
    if dest == (HOST,PORT):#Reach destination
        socket_ack = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        ack_num = data_list[-1]#extract ack number
        checksum_record = data_list[-2]
        data_l = len(data_list)
        checksum = str(sum(bytearray(" ".join(data_list[5:data_l-2]))))#extract portion of content
        print "receive msg CHECKSUM ==== "+ str(checksum)
        print "receive msg CHECKSUM 's RECORD==== "+ str(checksum_record)
        if checksum == checksum_record:#only ack when checksum match
            print "checksum right"
            print "File: "+file_name +"seq_No.: "+ seq + " from " +str(source)+ " safely received. Transfer done"
            source_ip = data_list[3].split(":")[0]
            source_port = int(data_list[3].split(":")[1])
            ack_msg = "%" + ack_num
            #send back ack
            socket_ack.sendto( ack_msg,(source_ip,source_port))
            socket_ack.close()
            data_list.pop()
            data_list.pop()#get rid of  ack number and checksum.
            recv_content = " ".join(data_list[5:data_l-2])
            recv_content_seq = seq
            BIG_CONTENT_DICT_LOCK.acquire()
            BIG_CONTENT_DICT[file_name].append((recv_content_seq,recv_content))
            BIG_CONTENT_DICT_LOCK.release()
            READY_TO_WRITE = True#this is aprroximating mechanism.
        else:
            print "checksum wrong"
    else:#Reach hops 
        DV_INFO_LOCK.acquire()
        next_hop = DV_NXT_HOP[dest]
        data_list[0] = "@"+file_name
        data_list[2] = next_hop[0]+":"+next_hop[1]
        print "File: " + file_name + "seq_No.: " + seq + " to "+ str(dest)+" from "+ str(source)+  " thru next hop:"+ data_list[2]+" is passing on"
        data_package_to_relay = " ".join(data_list)
        OUTPOOL_LOCK.acquire()
        OUTPOOL.append(data_package_to_relay)
        OUTPOOL_LOCK.release()
        DV_INFO_LOCK.release()


def ack_increment(info_to_send):#This basic increment function is used for TRANSFER file
    global ACK_NUM
    #DATA_WAITING_FROACK_LOCK will be required and released within transfer_big_data
    print ACK_NUM
    ack_increment_info = info_to_send+" "+str(ACK_NUM)
    DATA_WAITING_FOR_ACK[ACK_NUM] = (ack_increment_info,time.time())
    ACK_NUM += 1
    return ack_increment_info

def check_proxy(nexthop):
    global PROXY_PORT
    global PROXY_IP
    global PROXY_NEIGHBORS_LOCK
    global PROXY_NEIGHBORS
    ip = nexthop[0]
    port = str(nexthop[1])
    nexthop = (ip,port)#right format of key now
    PROXY_NEIGHBORS_LOCK.acquire()
    if nexthop in PROXY_NEIGHBORS:
        print str(nexthop) + "MUST GO THRU PROXY.........."
        PROXY_NEIGHBORS_LOCK.release()
        return True
    else:
        PROXY_NEIGHBORS_LOCK.release()
        return False

#TODO I will use a separate thread to update DV, every time DV needs to be updated under two cases as instructed
#This will finally put a ["#",DV_INFO_COPY] into OUTPOOL which will sit there and wait for sending by sender thread
class DV_Main (threading.Thread):
    def __init__(self,recv_dv_info):
        threading.Thread.__init__(self)
        self.recv_dv_info = recv_dv_info
    def run(self):
        global HOST
        global PORT
        global BUFF
        global DV_INFO
        global NEIGHBORS_INFO
        global NEIGHBORS_ORIGIN
        global NEIGHBORS_PREVIOUS
        global  NEIGHBORS_TIMER_INFO_LOCK
        global  NEIGHBORS_TIMER_INFO
        global DV_INFO_LOCK
        global DV_NXT_HOP
       # ===========================DV_miantain caused by command========================================================
        if self.recv_dv_info[0] == '$':
            recv_dv_info_list = self.recv_dv_info[1:].split(" ")
            command = recv_dv_info_list[0]
            if command == "CLOSE":#Notice when you receive this, doesn't mean I am closing, this means one of my neighbors is closing.
                ID = tuple(recv_dv_info_list[1].split(":"))
                DV_INFO_LOCK.acquire()
                print "Bye---------------------------------"+str(ID)
                #A closed node will be unreachable
                DV_INFO[ID] = "Infinity"
                DV_NXT_HOP[ID] = None
                NEIGHBORS_PREVIOUS[ID] = NEIGHBORS_ORIGIN[ID]#this is the critical part to coordinate CLOSE wit LINKDOWN
                NEIGHBORS_INFO[ID] = "Infinity"
                DV_INFO_LOCK.release()
                return
            if command == "CHANGECOST":
                print "I am processing CHANGECOST"
                ID = tuple(recv_dv_info_list[1].split(":"))
                link_cost = float(recv_dv_info_list[2])
                DV_INFO_LOCK.acquire()
                old_cost = DV_INFO[ID]
                cost_diff = link_cost - old_cost
                if link_cost < DV_INFO[ID]:#Optimization change to better way to ID
                    DV_NXT_HOP[ID] = ID
                    DV_INFO[ID] = link_cost
                else:
                    if DV_NXT_HOP[ID] == ID :
                        DV_INFO[ID] = link_cost

                NEIGHBORS_INFO[ID] = link_cost
                NEIGHBORS_PREVIOUS[ID] = link_cost
                for dest,first_hop in DV_NXT_HOP.items():
                    if dest != ID and first_hop == ID:
                        DV_INFO[dest] += cost_diff
                print ">>>>>>>>>>>>link cost change<<<<<<<<<<"
                print ID, NEIGHBORS_INFO[ID]
                print ">>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<"
                dv_data_to_send = pack((HOST,PORT),DV_INFO)
                DV_INFO_LOCK.release()
                OUTPOOL_LOCK.acquire()
                OUTPOOL.append(dv_data_to_send)
                OUTPOOL_LOCK.release()
                return
            if command == "LINKDOWN":
                flag = 0
                print "I am processing LINKDOWN"
                ID = tuple(recv_dv_info_list[1].split(":"))
                DV_INFO_LOCK.acquire()
                NEIGHBORS_INFO[ID] = "Infinity"
                for dest,first_hop in DV_NXT_HOP.items():
                    if first_hop == ID:
                        DV_INFO[dest] = "Infinity"
                        DV_NXT_HOP[dest] = None
                        print ">>>>>>>>>>>>Due to "+ str(ID)  +" link down<<<<<<<<<<"
                        print dest, DV_INFO[dest]
                        print ">>>>>>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<<<<<<<"
                        flag = 1
                if flag:
                    dv_data_to_send = pack((HOST,PORT),DV_INFO)
                    OUTPOOL_LOCK.acquire()
                    OUTPOOL.append(dv_data_to_send)
                    OUTPOOL_LOCK.release()
                DV_INFO_LOCK.release()
                return
            if command == "LINKUP":
                flag = 0
                print "I am processing LINKUP"
                ID = tuple(recv_dv_info_list[1].split(":"))
                DV_INFO_LOCK.acquire()
                #This is the only place NEIGHBORS_PREVIOUS is useful
                NEIGHBORS_INFO[ID] = NEIGHBORS_PREVIOUS[ID]
                print ">>>>>>>>>>>>"+ str(ID) +" link up<<<<<<<<<<"
                print " "
                print ">>>>>>>>>>>>>>>>>>><<<<<<<<<<<<<<<<<<<<<<<<"
                if DV_INFO[ID] == "Infinity":
                    flag = 1
                    DV_INFO[ID] = NEIGHBORS_INFO[ID]
                    DV_NXT_HOP[ID] = ID
                else:
                    if DV_INFO[ID] > NEIGHBORS_INFO[ID]:
                        flag =1
                        DV_INFO[ID] = NEIGHBORS_INFO[ID]
                        DV_NXT_HOP[ID] = ID
                if flag:
                    dv_data_to_send = pack((HOST,PORT),DV_INFO)
                    OUTPOOL_LOCK.acquire()
                    OUTPOOL.append(dv_data_to_send)
                    OUTPOOL_LOCK.release()
                DV_INFO_LOCK.release()
                return
        #==================================DV miantain caused by ROUTE MSG==============================================
        if self.recv_dv_info[0] == '#':
            flag = 0#indicates whether dv has been changed or not
            print "Process ROUTEMSG...in DV_main()"
            #print self.recv_dv_info
            ID, hashtable = unpack(self.recv_dv_info)
            NEIGHBORS_TIMER_INFO[ID] = time.time()#reset timer for the neighbor
            DV_INFO_LOCK.acquire()
            #---------------------------------First check conditions of two sides of the link---------------------------
            if DV_INFO[ID] == "Infinity":
                print "\nA Unreachable Neighbor Node Now Restores"
                if NEIGHBORS_INFO[ID] != "Infinity":
                    print "\nCheers...Arise in time to avoid 3*TIMEOUT"
                    DV_INFO[ID] = NEIGHBORS_INFO[ID]
                    DV_NXT_HOP[ID] = ID
                else:
                    print "BACK TO ORIGIN..You see this because you CLOSE before"
                    NEIGHBORS_INFO[ID] = NEIGHBORS_ORIGIN[ID]
                    NEIGHBORS_PREVIOUS[ID] = NEIGHBORS_ORIGIN[ID]
                    NEIGHBORS_TIMER_INFO_LOCK.acquire()
                    NEIGHBORS_TIMER_INFO[ID] = time.time()
                    NEIGHBORS_TIMER_INFO_LOCK.release()
                    DV_INFO[ID] = NEIGHBORS_INFO[ID]
                    DV_NXT_HOP[ID] = ID

            else:
                if DV_INFO[ID] != hashtable[(HOST,PORT)]:
                    if NEIGHBORS_INFO[ID] != "Infinity":
                        if DV_INFO[ID] < NEIGHBORS_INFO[ID]:
                #You can do NOTHING here. Basically, You can't update A's dv[B]  based on B's dv[A]
                            print "_____________I Observe Better Chance But can not do anything______________"+ str(ID)
                        else:
                            print "_____________I Observe Better Chance Let's Wait No More and Take it______________"+ str(ID)
                            DV_INFO[ID] = NEIGHBORS_INFO[ID]
                            DV_NXT_HOP[ID] = ID
                    else:
                        print "BACK TO ORIGIN...you see this because you CLOSE before"
                        NEIGHBORS_INFO[ID] = NEIGHBORS_ORIGIN[ID]
                        NEIGHBORS_PREVIOUS[ID] = NEIGHBORS_ORIGIN[ID]
                        NEIGHBORS_TIMER_INFO_LOCK.acquire()
                        NEIGHBORS_TIMER_INFO[ID] = time.time()
                        NEIGHBORS_TIMER_INFO_LOCK.release()
                        if DV_INFO[ID] > NEIGHBORS_INFO[ID]:
                            DV_INFO[ID] = NEIGHBORS_INFO[ID]
                            DV_NXT_HOP[ID] = ID

                else:
                    if DV_INFO[ID] > NEIGHBORS_INFO[ID]:#this is more like optimization
                        print "_____________ Adjust to Go Directly to Neighbor ______________"+ str(ID)
                        DV_INFO[ID] = NEIGHBORS_INFO[ID]#NEIGHBORS_ORIGIN[ID]
                        DV_NXT_HOP[ID] = ID
            del hashtable[(HOST,PORT)]#this is the link info of a neighbor and 'myself' which is useless now

            #------------------------------------------Regular Maintain--------------------------------------------------
            for dest in hashtable:
                if dest not in DV_INFO:
                    flag = 1
                    if hashtable[dest] != "Infinity":#this indicates new non-neighbor node(from perspective of myself) in the topology
                        DV_INFO[dest] = DV_INFO[ID] + hashtable[dest]
                        DV_NXT_HOP[dest] = DV_NXT_HOP[ID]
                    else:#this is weird situation, but exits due to my design of CLOSE
                       #print "This May Occur CLOSE and then restart!"
                       DV_INFO[dest] = "Infinity"
                       DV_NXT_HOP[dest] = None
                else:
                    if hashtable[dest] != "Infinity":
                        if DV_INFO[dest] == "Infinity":#Now dest is reachable for 'myself'
                            flag = 1
                            DV_INFO[dest] = DV_INFO[ID] + hashtable[dest]
                            DV_NXT_HOP[dest] = DV_NXT_HOP[ID]
                            if dest in NEIGHBORS_INFO and NEIGHBORS_INFO[dest] != "Infinity" and DV_INFO[dest] > NEIGHBORS_INFO[dest]:
                                DV_INFO[dest] = NEIGHBORS_INFO[dest]
                                DV_NXT_HOP[dest] = dest
                        else:
                            if DV_INFO[dest] > DV_INFO[ID] + hashtable[dest]:#link release
                                flag = 1
                                print "$$$$$$$$$$$$LINK RELEASE$$$$$$$$$$$$$$$$$$$$"
                                DV_INFO[dest] = DV_INFO[ID] + hashtable[dest]
                                DV_NXT_HOP[dest] = DV_NXT_HOP[ID]
                                print "release to" +str(dest) + "COST: "+ str(DV_INFO[dest])
                                print "thru" + str(DV_NXT_HOP[dest])
                            elif DV_NXT_HOP[dest] == ID and DV_INFO[dest] < DV_INFO[ID] + hashtable[dest] :
                                flag = 1
                                print "++++++++++++SELF CORRECTION on COST++++++++++++++++++++++"
                                DV_INFO[dest] = DV_INFO[ID] + hashtable[dest]
                    elif hashtable[dest] == "Infinity":
                        if dest in DV_NXT_HOP and DV_NXT_HOP[dest] == ID:
                            print "++++++++++++SELF CORRECTION on REACHABILITY to +++++++++++++++"+str(dest)
                            flag = 1
                            DV_INFO[dest] = "Infinity"
                            DV_NXT_HOP[dest] = None

            DV_INFO_LOCK.release()
        if flag:
            print "UPDATE DV_INFO...in DV_main"
            dv_data_to_send = pack((HOST,PORT),DV_INFO)
            OUTPOOL_LOCK.acquire()
            OUTPOOL.append(dv_data_to_send)
            OUTPOOL_LOCK.release()



#TODO I will use a separate thread Sender to send data(file data or DV data)
class Sender (threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
    def run(self):
        global HOST
        global PORT
        global DV_INFO
        global DV_INFO_LOCK
        global NEIGHBORS_INFO
        global OUTPOOL_LOCK
        global BUFF

        socket_send_to_neighbor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        socket_send_to_neighbor.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, BUFF)
        socket_send_to_proxy = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        socket_send_to_proxy.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, BUFF)
        while 1:
            OUTPOOL_LOCK.acquire()
            if len(OUTPOOL) > 0:
                info_to_send = OUTPOOL.pop(0)
                if info_to_send[0] == '#':#This is ROUTEUPDATE message
                    for dest in NEIGHBORS_INFO.keys():
                        if NEIGHBORS_INFO[dest] == "Infinity":
                            continue
                        neighbor_host = dest[0]
                        neighbor_port = int(dest[1])
                        info_to_send_poison_reverse = poison_reverse(dest,info_to_send)#apply poison reverse on dv sending to a specific neighbor
                        socket_send_to_neighbor.sendto(info_to_send_poison_reverse,(neighbor_host,neighbor_port))
                        print "dv_to_send is sending in Sender()...to "+ neighbor_host + ":"+ str(neighbor_port)
                if info_to_send[0] == '$':
                    info_to_send_list = info_to_send[1:].split(" ")
                    if info_to_send_list[0] == "CHANGECOST":
                        command_label = info_to_send_list[0]
                        dest = tuple(info_to_send_list[1].split(":"))
                        neighbor_host = dest[0]
                        neighbor_port = int(dest[1])
                        link_cost = info_to_send_list[2]
                        command_info_to_send="$"+command_label+" "+HOST+":"+PORT+" "+link_cost#Notice this is to be sent to the neighbor which would be effected by this change of link cost, so ID part should refer to clnt0
                        socket_send_to_neighbor.sendto(command_info_to_send,(neighbor_host,neighbor_port))
                        print "command_to_CHANGECOST_send is sending in Sender()...to "+ neighbor_host + ":"+ str(neighbor_port)
                    if info_to_send_list[0] == "LINKDOWN" or info_to_send_list[0] == "LINKUP":
                        command_label = info_to_send_list[0]
                        dest = tuple(info_to_send_list[1].split(":"))
                        neighbor_host = dest[0]
                        neighbor_port = int(dest[1])
                        command_info_to_send="$"+command_label+" "+HOST+":"+PORT+" "#Notice this is to be sent to the neighbor which would be effected by this change of link cost, so ID part should refer to clnt0
                        socket_send_to_neighbor.sendto(command_info_to_send,(neighbor_host,neighbor_port))
                        print "command_to_LINKUP/DOWN_send is sending in Sender()...to "+ neighbor_host + ":"+ str(neighbor_port)
                    if info_to_send_list[0] == "CLOSE":
                        for dest in NEIGHBORS_INFO.keys():
                            neighbor_host = dest[0]
                            neighbor_port = int(dest[1])


                            socket_send_to_neighbor.sendto(info_to_send,(neighbor_host,neighbor_port))
                if info_to_send[0] == "@":#file transfer
                    info_to_send_list = info_to_send[1:].split(" ")
                    next_hop = info_to_send_list[2].split(":")
                    next_hop_ip = next_hop[0]
                    next_hop_port = int(next_hop[1])
                    if check_proxy((next_hop_ip,next_hop_port)):
                        socket_send_to_proxy.sendto(info_to_send,(PROXY_IP,int(PROXY_PORT)))
                    else:
                        socket_send_to_neighbor.sendto(info_to_send,(next_hop_ip,next_hop_port))

            OUTPOOL_LOCK.release()


class Timer(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
    def run(self):
        global HOST
        global PORT
        global NEIGHBORS_TIMER_INFO
        global DV_INFO
        global DV_NXT_HOP
        global NEIGHBORS_INFO
        global NEIGHBORS_PREVIOUS
        global OUTPOOL
        global OUTPOOL_LOCK
        while 1:
            time.sleep(TIMEOUT)
            heartbeat_dv_update = pack((HOST,PORT),DV_INFO)#In my current design, it is possible to send dv_info many times within TIMEOUT. Here I only ensure at least one update will happen if current host is still alive
            print "Timer dv update"+heartbeat_dv_update
            OUTPOOL_LOCK.acquire()
            OUTPOOL.append(heartbeat_dv_update)
            OUTPOOL_LOCK.release()

            check_point = time.time()
            DV_INFO_LOCK.acquire()
            for neighbor,time_last_update in NEIGHBORS_TIMER_INFO.items():
                if NEIGHBORS_INFO[neighbor] == "Infinity":#This is already a unreachable neighbor
                    #print "\n 3*TIMEOUT exception happen" + str(neighbor) + "already set to Infinity"
                    continue
                if check_point - time_last_update >= 3*TIMEOUT:
                    print "\nA 3*TIMEOUT exception happen" + str(neighbor) + "set to Infinity"
                    DV_INFO[neighbor] = "Infinity" #this must be the case. The only trigger for TIMEOUT*3 is CLOSE which means that node doesn't exist anymore.
                    DV_NXT_HOP[neighbor] = None
                    NEIGHBORS_INFO[neighbor] = "Infinity"
                    NEIGHBORS_PREVIOUS[neighbor] = NEIGHBORS_ORIGIN[neighbor]#when a node quits the topology, to me, previous link cost is useless and should be set back to origin link cost.
                    NEIGHBORS_TIMER_INFO[neighbor] = time.time()
                    for dest,first_hop in DV_NXT_HOP.items():
                        if first_hop == neighbor:
                            DV_INFO[dest] = "Infinity"
                            DV_NXT_HOP[dest] = None
                    dv_info_to_send = pack((HOST,PORT),DV_INFO)#update dv_info
                    OUTPOOL_LOCK.acquire()
                    OUTPOOL.append(dv_info_to_send)
                    OUTPOOL_LOCK.release()
            DV_INFO_LOCK.release()

class Timer_ACK(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
    def run(self):
        global DATA_WAITING_FOR_ACK_LOCK
        global DATA_WAITING_FOR_ACK
        global OUTPOOL
        global OUTPOOL_LOCK
        global TIMEOUT_ACK_GIVEUP
        global TIMEOUT_ACK_RESEND
        while 1:
            time.sleep(TIMEOUT_ACK_RESEND)
            DATA_WAITING_FOR_ACK_LOCK.acquire()
            OUTPOOL_LOCK.acquire()
            for msg_ack in DATA_WAITING_FOR_ACK:
                if DATA_WAITING_FOR_ACK[msg_ack]:
                    print "ROUTINE CHECKing of non-ACK MSG"
                    time_current = time.time()
                    time_sent = DATA_WAITING_FOR_ACK[msg_ack][1]
                    data_sent = DATA_WAITING_FOR_ACK[msg_ack][0]
                    if time_current - time_sent > TIMEOUT_ACK_RESEND:
                        if time_current - time_sent > TIMEOUT_ACK_GIVEUP:
                            print "!!!TIMEOUT: GIVEUP ON UN-ACK MSG!!!"
                            DATA_WAITING_FOR_ACK[msg_ack] = None
                        else:
                            print "!!!RESEND UN-ACK MSG!!!"
                            OUTPOOL.append(data_sent)
            OUTPOOL_LOCK.release()
            DATA_WAITING_FOR_ACK_LOCK.release()

class Writeout_Big_FIle(threading.Thread):
    def __init__(self):
        threading.Thread.__init__(self)
    def run(self):
        global READY_TO_WRITE
        global BIG_CONTENT_DICT_LOCK
        global BIG_CONTENT_DICT
        while 1:
            if READY_TO_WRITE:
                time.sleep(10)#Approximate comprise mechanism because lack of time, assume file will be fully received within 10 mins
                BIG_CONTENT_DICT_LOCK.acquire()
                if len(BIG_CONTENT_DICT.keys()) > 0 :
                    file_name_list = list(BIG_CONTENT_DICT.keys())
                    print "^^^^^^^^^^^^^^^^^^^^^^^^"
                    print"Write Out"
                    print "^^^^^^^^^^^^^^^^^^^^^^^^"
                    for file_name in file_name_list:
                        seq_content_list = BIG_CONTENT_DICT[file_name]
                        file_name_l = file_name.split(".")
                        file_name_l[0] = "newcopy"
                        file_name_new = ".".join(file_name_l)
                        seq_content_list = sorted(seq_content_list,key=lambda l:l[0])
                        file_to_write = open(file_name_new,"w")
                        for seq_content in seq_content_list:
                            file_to_write.write(seq_content[1])
                        file_to_write.close()
                        del BIG_CONTENT_DICT[file_name]
                BIG_CONTENT_DICT_LOCK.release()
            READY_TO_WRITE = False
#TODO I will of course also have a main thread
#main thread
def main(argv):
    global HOST
    global PORT
    global PROXY_IP
    global PROXY_PORT
    global BUFF
    global DV_INFO
    global DV_INFO_LOCK
    global NEIGHBORS_INFO
    global TIMEOUT
    global TIMEFORMAT
    global NEIGHBORS_TIMER_INFO
    global NEIGHBORS_TIMER_INFO_LOCK#this may be not needed
    global NEIGHBORS_ORIGIN
    global NEIGHBORS_PREVIOUS
#main thread takes input from both system.input and listening socket through select. However, sending business is under
#the charge of Sender thread.
    if len(argv) != 2:
        sys.exit("clnt asks for config file to start---> ./bfclient clnt_config.txt")

    #TODO process config_file : get ID info of self and all neighbors'
    conf_file = argv[1]
    conf_file_descriptor = open(conf_file, 'r')
    self_host_info = conf_file_descriptor.readline().strip()
    self_host_info = self_host_info.split(" ")
    #init listening UDP socket
    HOST = socket.gethostbyname(socket.gethostname())
    PORT = self_host_info[0]
    TIMEOUT = float(self_host_info[1])
    listening_socket =  socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recvbuff = BUFF
    listening_socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, recvbuff)
    listening_socket.bind((HOST, int(PORT)))
    print "LISTENING ON" + HOST+":"+PORT
    #init neighbor_host_info
    neighbor_info = conf_file_descriptor.readline().strip()
    DV_INFO_LOCK.acquire()
    while neighbor_info:
        neighbor_info = neighbor_info.split(" ")
        neighbor_ID_info = neighbor_info[0]
        neighbor_ID_tuple = tuple(neighbor_ID_info.split(":"))
        neighbor_weight_info = neighbor_info[1]
        NEIGHBORS_INFO[neighbor_ID_tuple] = float(neighbor_weight_info)
        #It is necessary to keep three NEIGHBROS tables, some of these three table will be almost same most time,
        #they differentiate when certain commands or situations occur.
        NEIGHBORS_PREVIOUS[neighbor_ID_tuple] = float(neighbor_weight_info)
        NEIGHBORS_ORIGIN[neighbor_ID_tuple] = float(neighbor_weight_info)
        DV_INFO[neighbor_ID_tuple] = float(neighbor_weight_info)
        DV_NXT_HOP[neighbor_ID_tuple] = neighbor_ID_tuple
        NEIGHBORS_TIMER_INFO[neighbor_ID_tuple] = time.time()
        neighbor_info = conf_file_descriptor.readline().strip()
    DV_INFO_LOCK.release()
    dv_to_send = pack((HOST,PORT),DV_INFO)#Pack DV_INFO, ready to be sent to neighbors
    OUTPOOL_LOCK.acquire()
    OUTPOOL.append(dv_to_send)
    OUTPOOL_LOCK.release()
    #Notice actually I only need READ_LIST in this thread
    READ_LIST = []
    WRITE_LIST = []
    EXCEPTION_LIST = []
    READ_LIST.append(listening_socket)
    READ_LIST.append(sys.stdin)
    sender_thread = Sender()
    sender_thread.setDaemon(True)
    sender_thread.start()
    timer_thread = Timer()
    timer_thread.setDaemon(True)
    timer_thread.start()
    ack_timer_thread = Timer_ACK()
    ack_timer_thread.setDaemon(True)
    ack_timer_thread.start()
    write_big_file_thread = Writeout_Big_FIle()
    write_big_file_thread.setDaemon(True)
    write_big_file_thread.start()
    while 1:
        readable_list, writable_list, exceptionable_list = select.select(READ_LIST, WRITE_LIST,EXCEPTION_LIST)
        for fd in readable_list:
            if fd == sys.stdin:
                command = (raw_input("[Issue your command(to see what commands are supported, type help)]:")).strip()
                #For now I assume right input format
                command_list = command.split(" ")
                command_label = command_list[0]
                if command_label == "CHANGECOST":
                    if NEIGHBORS_INFO[(command_list[1],command_list[2])] == "Infinity":
                        print "______________________________________________"
                        print "No Change on NON-Reachable Node Could be Done"
                        print "______________________________________________"
                    else:
                        ID = command_list[1]+":"+command_list[2]
                        link_cost = command_list[3]
                        command_info = "$"+command_label+" "+ID+" "+link_cost#protocal: $CHANGECOST IP:HOST COST
                        OUTPOOL_LOCK.acquire()
                        OUTPOOL.append(command_info)#this command info requires updating on both sides of the link modified
                        OUTPOOL_LOCK.release()
                        dv_thread = DV_Main(command_info)
                        dv_thread.setDaemon(True)
                        dv_thread.run()
                if command_label == "LINKDOWN" or command_label == "LINKUP":
                    ID = command_list[1]+":"+command_list[2]
                    command_info = "$"+command_label+" "+ID + " "
                    OUTPOOL_LOCK.acquire()
                    OUTPOOL.append(command_info)#this command info requires updating on both sides of the link modified
                    OUTPOOL_LOCK.release()
                    dv_thread = DV_Main(command_info)
                    dv_thread.setDaemon(True)
                    dv_thread.run()

                if command_label == "CLOSE":
                     #   see details in function last_words()
                    last_words()
                    sys.exit("CLOSE ...")

                if command_label == "SHOWRT":
                    show_chart()
                if command_label == "TRANSFER":
                    file_name = command_list[1]
                    dest = (command_list[2],command_list[3])
                    transfer_big_file(file_name,dest)
                if command_label == "ADDPROXY":
                    PROXY_IP = command_list[1]
                    PROXY_PORT = command_list[2]
                    neighbor_info = (command_list[3],command_list[4])
                    PROXY_NEIGHBORS_LOCK.acquire()
                    PROXY_NEIGHBORS[neighbor_info] = True
                    PROXY_NEIGHBORS_LOCK.release()
                    print "aad proxy b/t" + str(neighbor_info)
                if command_label == "REMOVEPROXY" :
                    neighbor_info = (command_list[1],command_list[2])
                    PROXY_NEIGHBORS_LOCK.acquire()
                    del PROXY_NEIGHBORS[neighbor_info]
                    PROXY_NEIGHBORS_LOCK.release()
                    print "remove proxy b/t" + str(neighbor_info)

            else:
                #print "Info is coming thru UDP socket.."
                recv_data,addr = fd.recvfrom(BUFF)
                if recv_data[0] == '#':#this is ROUTE_UPDATE msg
                    print "I got ROUTEMSG from neighbors!"
                    dv_thread = DV_Main(recv_data)
                    dv_thread.setDaemon(True)
                    dv_thread.run()
                elif recv_data[0] == '$':#this is commands msg which include LINKDOWN,LINKUP,CLOSE
                    print "I got COMMAND MSG from neighbors!"
                    dv_thread = DV_Main(recv_data)
                    dv_thread.setDaemon(True)
                    dv_thread.run()
                elif recv_data[0] == '@':#this is file_transfer package msg
                    relay_race(recv_data)
                elif recv_data[0] == "%":#this is ack msg protocol/msg
                    ack_feedback = int(recv_data[1:])
                    print "ACK : " +str(ack_feedback)+" received"
                    DATA_WAITING_FOR_ACK_LOCK.acquire()
                    del DATA_WAITING_FOR_ACK[ack_feedback]
                    DATA_WAITING_FOR_ACK_LOCK.release()                   



if __name__ == '__main__':

        main(sys.argv)

#  Test case passed on a 5 nodes topology having loops within loops (see readme)
# CLOSE
# CHANGECOST 192.168.0.19 4118 1
# CHANGECOST 192.168.0.19 4118 30
# LINKDOWN 192.168.0.19 4118
# LINKUP 192.168.0.19 4118
# TRANSFER test.jpg 192.168.0.19 4117
# ADDPROXY 192.168.0.2 41192 192.168.0.19 4119
# REMOVEPROXY 192.168.0.19 4119